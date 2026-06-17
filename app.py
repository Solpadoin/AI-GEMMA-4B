from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_runtime import AgentRuntime

try:
    from llama_cpp import Llama
except Exception:  # pragma: no cover - keeps config endpoints usable before install.
    Llama = None


APP_ROOT = Path(__file__).resolve().parent
DATA_DIR = APP_ROOT / "data" / "projects"
SETTINGS_FILE = APP_ROOT / "data" / "settings.json"
STATIC_DIR = APP_ROOT / "static"
MODEL_PATH = Path(os.getenv("MODEL_PATH", APP_ROOT / "models" / "gemma-4-12B-it-Q4_K_M.gguf"))
WORKSPACE_ROOT = Path(os.getenv("AGENT_WORKSPACE", APP_ROOT)).resolve()
LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "http://127.0.0.1:8080").rstrip("/")
DEFAULT_SETTINGS = {
    "model_name": os.getenv("MODEL_NAME", "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"),
    "thinking": os.getenv("ENABLE_THINKING", "0").lower() in {"1", "true", "yes", "on"},
    "temperature": float(os.getenv("TEMPERATURE", "1.0")),
    "top_p": float(os.getenv("TOP_P", "0.95")),
    "top_k": int(os.getenv("TOP_K", "64")),
    "max_tokens": int(os.getenv("MAX_TOKENS", "2048")),
    "access_mode": os.getenv("ACCESS_MODE", "confirm"),
    "agent_mode": os.getenv("AGENT_MODE", "1").lower() in {"1", "true", "yes", "on"},
    "max_tool_steps": int(os.getenv("MAX_TOOL_STEPS", "8")),
    "web_search": os.getenv("WEB_SEARCH", "1").lower() in {"1", "true", "yes", "on"},
}

SYSTEM_PROMPT = """You are a careful local coding assistant.
You are running on the user's Windows PC through a local tool bridge.
You CAN inspect local Windows paths such as C:\\Users\\Admin\\Documents\\... when the tool policy allows it.
You CAN edit files, run PowerShell commands, use git, fetch web pages, open URLs, and search the web through approved actions.
Never say that you cannot access the user's local disk, PC, files, or repository when a path is provided. Use a tool instead.
When the user asks you to change code, push git changes, or work with the PC, proactively use tools until the task is actually finished.
Never claim you executed a command, changed a file, committed, pushed git changes, or opened a browser unless a tool result says so.
If you need a tool, end your answer with exactly one fenced block:
```agent_action
{"tool":"read_file","path":"relative/path.txt"}
```
Supported tools: list_files, search_in_files, read_file, write_file, replace_in_file, run_command, search_web, fetch_url, browser_read, inspect_image, open_url.
search_in_files requires path and query. write_file requires path and content. replace_in_file requires path, old, and new. run_command requires command and may include cwd. search_web requires query. fetch_url/browser_read/open_url require url. inspect_image requires path.
Windows absolute paths are valid in tool JSON, for example:
```agent_action
{"tool":"list_files","path":"C:\\Users\\Admin\\Documents\\ZM 4.3\\CS1.6-ZM-WEBSITE"}
```
For a repository edit plus push, inspect/search first, edit, run git diff/status, commit, push, then give a concise final result.
For git commands in a user-provided repository path, set run_command.cwd to that exact repository path.
Use search_web when current external information or documentation is needed.
Prefer small, reversible actions. In auto modes, keep going after each tool result until you can give the final answer."""

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Fable5 Local Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_llm: Any | None = None
runtime = AgentRuntime(WORKSPACE_ROOT, load_settings=lambda: load_settings())


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    approved: bool


class SettingsUpdate(BaseModel):
    thinking: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=200)
    max_tokens: int | None = Field(default=None, ge=16, le=4096)
    access_mode: Literal["confirm", "auto_read", "auto_all"] | None = None
    agent_mode: bool | None = None
    max_tool_steps: int | None = Field(default=None, ge=0, le=12)
    web_search: bool | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return dict(DEFAULT_SETTINGS)
    settings = dict(DEFAULT_SETTINGS)
    settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    return settings


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings


def system_prompt(settings: dict[str, Any]) -> str:
    prompt = SYSTEM_PROMPT
    prompt += "\nWeb search is currently " + ("enabled." if settings.get("web_search", True) else "disabled. Do not use search_web.")
    prompt += "\nRuntime capabilities:\n" + json.dumps(runtime.capabilities(), ensure_ascii=False)
    if settings.get("thinking"):
        return "<|think|>\n" + prompt
    return prompt


def project_path(project_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", project_id):
        raise HTTPException(status_code=400, detail="Bad project id")
    return DATA_DIR / f"{project_id}.json"


def load_project(project_id: str) -> dict[str, Any]:
    path = project_path(project_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return json.loads(path.read_text(encoding="utf-8"))


def save_project(project: dict[str, Any]) -> None:
    project["updated_at"] = now_iso()
    project_path(project["id"]).write_text(
        json.dumps(project, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    visible_messages = [msg for msg in project.get("messages", []) if not msg.get("hidden") and msg.get("role") != "tool"]
    return {
        "id": project["id"],
        "name": project["name"],
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
        "messages": len(visible_messages),
        "pending_action": project.get("pending_action"),
        "has_summary": bool(project.get("summary")),
    }


def get_llm() -> Any:
    global _llm
    if _llm is not None:
        return _llm
    if Llama is None:
        raise HTTPException(status_code=500, detail="llama-cpp-python is not installed")
    if not MODEL_PATH.exists():
        raise HTTPException(status_code=500, detail=f"Model not found: {MODEL_PATH}")

    _llm = Llama(
        model_path=str(MODEL_PATH),
        n_ctx=int(os.getenv("N_CTX", "8192")),
        n_threads=int(os.getenv("N_THREADS", str(os.cpu_count() or 4))),
        n_gpu_layers=int(os.getenv("N_GPU_LAYERS", "0")),
        verbose=False,
    )
    return _llm


def messages_to_prompt(messages: list[dict[str, str]]) -> str:
    role_names = {"system": "System", "user": "User", "assistant": "Assistant", "tool": "Tool"}
    parts = []
    for message in messages:
        role = role_names.get(message["role"], message["role"].title())
        parts.append(f"{role}:\n{message['content']}")
    parts.append("Assistant:\n")
    return "\n\n".join(parts)


def wants_json_response(messages: list[dict[str, str]]) -> bool:
    if not messages:
        return False
    system = messages[0].get("content", "").lower()
    return "single json object" in system and "tool" in system


def create_chat_completion(messages: list[dict[str, str]], settings: dict[str, Any] | None = None) -> str:
    settings = settings or load_settings()
    if LLAMA_SERVER_URL:
        payload = {
            "model": settings["model_name"],
            "messages": messages,
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "top_k": settings["top_k"],
            "max_tokens": settings["max_tokens"],
        }
        if wants_json_response(messages):
            payload["response_format"] = {"type": "json_object"}
        try:
            response = httpx.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json=payload,
                timeout=300,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPError as exc:
            chat_error = exc

        try:
            response = httpx.post(
                f"{LLAMA_SERVER_URL}/v1/completions",
                json={
                    "model": payload["model"],
                    "prompt": messages_to_prompt(messages),
                    "temperature": payload["temperature"],
                    "max_tokens": payload["max_tokens"],
                    "stop": ["\nUser:", "\nSystem:", "\nTool:"],
                },
                timeout=300,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["text"].strip()
        except httpx.HTTPError as exc:
            detail = getattr(exc, "response", None)
            if detail is not None:
                raise HTTPException(status_code=502, detail=detail.text[:2000]) from exc
            raise HTTPException(status_code=502, detail=f"{chat_error}; {exc}") from exc

    response = get_llm().create_chat_completion(
        messages=messages,
        temperature=float(os.getenv("TEMPERATURE", "0.2")),
        max_tokens=int(os.getenv("MAX_TOKENS", "256")),
    )
    return response["choices"][0]["message"]["content"]


def stream_chat_completion(messages: list[dict[str, str]], settings: dict[str, Any] | None = None):
    settings = settings or load_settings()
    if not LLAMA_SERVER_URL:
        yield create_chat_completion(messages, settings)
        return

    payload = {
        "model": settings["model_name"],
        "messages": messages,
        "temperature": settings["temperature"],
        "top_p": settings["top_p"],
        "top_k": settings["top_k"],
        "max_tokens": settings["max_tokens"],
        "stream": True,
    }
    if wants_json_response(messages):
        payload["response_format"] = {"type": "json_object"}

    with httpx.Client(timeout=None) as client:
        try:
            with client.stream("POST", f"{LLAMA_SERVER_URL}/v1/chat/completions", json=payload) as response:
                response.raise_for_status()
                yield from iter_openai_stream(response)
                return
        except httpx.HTTPError:
            pass

        completion_payload = {
            "model": settings["model_name"],
            "prompt": messages_to_prompt(messages),
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "top_k": settings["top_k"],
            "max_tokens": settings["max_tokens"],
            "stream": True,
            "stop": ["\nUser:", "\nSystem:", "\nTool:"],
        }
        with client.stream("POST", f"{LLAMA_SERVER_URL}/v1/completions", json=completion_payload) as response:
            response.raise_for_status()
            yield from iter_openai_stream(response)


def iter_openai_stream(response: httpx.Response):
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data: "):
            line = line[6:]
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        choice = (data.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}
        token = delta.get("content")
        if token is None:
            token = choice.get("text")
        if token:
            yield token


def split_gemma_channels(text: str) -> dict[str, str]:
    normalized = text.replace("<channel|>", "<|end_channel|>")
    thought = ""
    answer = normalized
    match = re.search(r"<\|channel\>thought\s*(.*?)<\|end_channel\|>", normalized, flags=re.DOTALL)
    if match:
        thought = match.group(1).strip()
        answer = normalized[match.end() :]
    answer = re.sub(r"<\|/?(?:channel|end_channel|think)\|?>", "", answer)
    answer = answer.replace("<|channel>", "").replace("<|end_channel|>", "")
    return {"thinking": thought, "answer": answer.strip()}


def sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_messages(project: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt(settings)}]
    if project.get("summary"):
        messages.append({"role": "system", "content": "Conversation summary so far:\n" + project["summary"]})
    messages.extend({"role": msg["role"], "content": msg["content"]} for msg in project["messages"][-40:])
    return messages


def latest_user_text(project: dict[str, Any]) -> str:
    for message in reversed(project.get("messages", [])):
        if message.get("role") == "user":
            return str(message.get("content", ""))
    return ""


def tool_messages_since_latest_user(project: dict[str, Any]) -> list[dict[str, Any]]:
    messages = []
    for message in reversed(project.get("messages", [])):
        if message.get("role") == "user":
            return list(reversed(messages))
        if message.get("role") == "tool":
            messages.append(message)
    return list(reversed(messages))


def requested_windows_paths(text: str) -> list[str]:
    return [
        match.rstrip(" .,/;)")
        for match in re.findall(r"[A-Za-z]:\\[A-Za-z0-9 ._\\/\-()]+", text)
    ]


def action_uses_path(action: dict[str, Any], path: str) -> bool:
    normalized = path.lower().rstrip("\\/")
    values = [
        str(action.get("path", "")),
        str(action.get("cwd", "")),
        str(action.get("command", "")),
    ]
    return any(normalized in value.lower().rstrip("\\/") for value in values)


def final_evidence_error(project: dict[str, Any], final_action: dict[str, Any]) -> str | None:
    latest = latest_user_text(project)
    latest_lower = latest.lower()
    answer = str(final_action.get("answer", "")).lower()
    tools = tool_messages_since_latest_user(project)
    actions = [msg.get("action") or {} for msg in tools]
    tool_names = [str(action.get("tool", "")) for action in actions]
    tool_text = "\n".join(str(msg.get("content", "")) for msg in tools).lower()

    if "tool error" in tool_text:
        return "A previous tool call failed. Do not finish until you recover or clearly report the failure."

    url_requested = bool(re.search(r"https?://\S+", latest))
    if url_requested and not any(name in {"browser_read", "fetch_url"} for name in tool_names):
        return "The user asked about a URL. You must use browser_read or fetch_url before final."

    asks_commit = any(word in latest_lower for word in ("commit", "коммит", "закоммит", "заккомит", "заккомить"))
    asks_push = any(word in latest_lower for word in ("push", "запуш", "запушь", "пуш"))
    claims_commit = any(word in answer for word in ("committed", "commit", "закоммит", "закомич", "коммит"))
    claims_push = any(word in answer for word in ("pushed", "push", "запуш", "отправил"))

    git_commands = [str(action.get("command", "")).lower() for action in actions if action.get("tool") == "run_command"]
    if asks_commit or claims_commit:
        if not any("git status" in command for command in git_commands):
            return "Before claiming commit status, run git status in the requested repository."
        if asks_commit and not any("git commit" in command for command in git_commands):
            return "The user asked to commit changes. You must run git commit or explicitly prove there were no changes."

    if asks_push or claims_push:
        if not any("git status" in command for command in git_commands):
            return "Before claiming push status, run git status in the requested repository."
        if not any("git push" in command for command in git_commands):
            return "The user asked to push. You must run git push before final."

    paths = requested_windows_paths(latest)
    if (asks_commit or asks_push or claims_commit or claims_push) and paths:
        target = paths[0]
        if not any(action.get("tool") == "run_command" and action_uses_path(action, target) for action in actions):
            return f"Git commands must run in the requested repository path: {target}. Use run_command.cwd."

    return None


def append_protocol_error(project: dict[str, Any], content: str) -> None:
    project["messages"].append(
        {
            "role": "tool",
            "content": "Protocol error: " + content,
            "created_at": now_iso(),
            "hidden": True,
        }
    )


def run_agent_turn(project: dict[str, Any], settings: dict[str, Any]) -> str:
    final_text = ""
    project["pending_action"] = None
    max_steps = int(settings.get("max_tool_steps", 4))
    for step in range(max_steps + 1):
        agent_mode = bool(settings.get("agent_mode", True))
        messages = runtime.build_action_messages(project, settings) if agent_mode else build_messages(project, settings)
        assistant_text = create_chat_completion(messages, settings)
        final_text = assistant_text
        action = runtime.parse_action(assistant_text)
        if not action:
            if agent_mode and step < max_steps:
                project["messages"].append(
                    {
                        "role": "tool",
                        "content": "Protocol error: expected one JSON action object. Choose a tool or final.",
                        "created_at": now_iso(),
                        "hidden": True,
                    }
                )
                continue
            project["messages"].append({"role": "assistant", "content": assistant_text, "created_at": now_iso()})
            project["pending_action"] = None
            return final_text

        project["messages"].append({"role": "assistant", "content": assistant_text, "created_at": now_iso(), "hidden": True})
        if action.get("tool") == "final":
            error = final_evidence_error(project, action)
            if error and step < max_steps:
                append_protocol_error(project, error)
                continue
            project["messages"].append({"role": "assistant", "content": action.get("answer", ""), "created_at": now_iso()})
            project["pending_action"] = None
            return action.get("answer", "")

        if not runtime.can_auto_execute(action, settings):
            project["pending_action"] = action
            return final_text

        try:
            result = runtime.execute_action(action, unrestricted_paths=settings.get("access_mode") == "auto_all")
        except Exception as exc:
            result = f"Tool error for {action['tool']}: {exc}"
        runtime.append_tool_result(project, action, result, now_iso)

    project["messages"].append(
        {
            "role": "assistant",
            "content": "Reached max tool steps for this turn.",
            "created_at": now_iso(),
        }
    )
    return final_text


def looks_binary_text(content: str) -> bool:
    if "\x00" in content:
        return True
    if not content:
        return False
    sample = content[:4096]
    bad = sum(1 for char in sample if ord(char) < 32 and char not in "\r\n\t")
    return bad / max(len(sample), 1) > 0.02


def format_user_message(message: str, attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return message
    parts = [message, "\n\nAttached files:"]
    for item in attachments:
        name = item.get("name", "attachment")
        content = item.get("content", "")
        if item.get("binary") or looks_binary_text(content):
            size = item.get("size")
            file_type = item.get("type", "application/octet-stream")
            suffix = f", {size} bytes" if size else ""
            parts.append(
                f"\n--- {name} ---\n"
                f"[Binary file omitted: {file_type}{suffix}. The local text model cannot inspect image pixels without a vision/OCR tool.]"
            )
            continue
        parts.append(f"\n--- {name} ---\n{content[:60000]}")
    return "\n".join(parts)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def config() -> dict[str, Any]:
    settings = load_settings()
    return {
        "model_path": str(MODEL_PATH),
        "model_exists": MODEL_PATH.exists(),
        "model_name": settings["model_name"],
        "workspace_root": str(WORKSPACE_ROOT),
        "llama_server_url": LLAMA_SERVER_URL,
        "n_ctx": int(os.getenv("N_CTX", "8192")),
        "settings": settings,
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return load_settings()


@app.get("/api/capabilities")
def get_capabilities() -> dict[str, Any]:
    return runtime.capabilities()


@app.post("/api/settings")
def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
    settings = load_settings()
    update = payload.model_dump(exclude_none=True)
    settings.update(update)
    return save_settings(settings)


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    projects = []
    for path in DATA_DIR.glob("*.json"):
        projects.append(public_project(json.loads(path.read_text(encoding="utf-8"))))
    return sorted(projects, key=lambda item: item["updated_at"], reverse=True)


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    project = {
        "id": str(uuid.uuid4()),
        "name": payload.name.strip(),
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "messages": [],
        "pending_action": None,
        "summary": "",
    }
    save_project(project)
    return public_project(project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    return project


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, bool]:
    path = project_path(project_id)
    if path.exists():
        path.unlink()
    return {"ok": True}


@app.post("/api/projects/{project_id}/clear")
def clear_project(project_id: str) -> dict[str, Any]:
    project = load_project(project_id)
    project["messages"] = []
    project["pending_action"] = None
    project["summary"] = ""
    save_project(project)
    return project


@app.post("/api/projects/{project_id}/compact")
def compact_project(project_id: str) -> dict[str, Any]:
    settings = load_settings()
    project = load_project(project_id)
    visible = [msg for msg in project.get("messages", []) if not msg.get("hidden") and msg.get("role") != "tool"]
    if len(visible) < 8:
        raise HTTPException(status_code=400, detail="Not enough visible messages to compact yet.")

    keep = visible[-6:]
    old = visible[:-6]
    transcript = "\n\n".join(f"{msg['role'].upper()}:\n{msg['content']}" for msg in old)[-30000:]
    previous = project.get("summary", "")
    summary_prompt = [
        {
            "role": "system",
            "content": (
                "Summarize this conversation for future context. Keep concrete user goals, decisions, file paths, "
                "commands/results, bugs, and unresolved tasks. Do not invent facts. Keep it concise."
            ),
        },
        {
            "role": "user",
            "content": (f"Previous summary:\n{previous}\n\n" if previous else "") + f"Conversation to compress:\n{transcript}",
        },
    ]
    summary = create_chat_completion(summary_prompt, {**settings, "max_tokens": min(settings.get("max_tokens", 2048), 900)})
    project["summary"] = split_gemma_channels(summary)["answer"] or summary
    project["messages"] = keep
    project["pending_action"] = None
    save_project(project)
    return project


@app.post("/api/projects/{project_id}/chat")
def chat(project_id: str, payload: ChatRequest) -> dict[str, Any]:
    settings = load_settings()
    project = load_project(project_id)
    project["messages"].append(
        {"role": "user", "content": format_user_message(payload.message, payload.attachments), "created_at": now_iso()}
    )

    run_agent_turn(project, settings)
    save_project(project)
    return project


@app.post("/api/projects/{project_id}/chat/stream")
def chat_stream(project_id: str, payload: ChatRequest) -> StreamingResponse:
    settings = load_settings()
    project = load_project(project_id)
    project["messages"].append(
        {"role": "user", "content": format_user_message(payload.message, payload.attachments), "created_at": now_iso()}
    )
    project["pending_action"] = None
    save_project(project)

    def generate():
        yield sse_event("start", {"project_id": project_id})
        max_steps = int(settings.get("max_tool_steps", 4))

        for step in range(max_steps + 1):
            project_step = load_project(project_id)
            full_text = ""
            agent_mode = bool(settings.get("agent_mode", True))
            messages = runtime.build_action_messages(project_step, settings) if agent_mode else build_messages(project_step, settings)
            try:
                for token in stream_chat_completion(messages, settings):
                    full_text += token
                    parsed = split_gemma_channels(full_text)
                    yield sse_event(
                        "token",
                        {
                            "token": token,
                            "raw": full_text,
                            "answer": "" if agent_mode else parsed["answer"],
                            "thinking": parsed["thinking"],
                            "step": step,
                        },
                    )
            except Exception as exc:
                yield sse_event("error", {"detail": str(exc)})
                return

            pending_action = runtime.parse_action(full_text)
            project_done = load_project(project_id)
            if not pending_action and agent_mode and step < max_steps:
                project_done["messages"].append(
                    {
                        "role": "tool",
                        "content": "Protocol error: expected one JSON action object. Choose a tool or final.",
                        "created_at": now_iso(),
                        "hidden": True,
                    }
                )
                save_project(project_done)
                yield sse_event("tool", {"tool": "protocol", "status": "error", "summary": "Invalid tool-call format"})
                continue

            if pending_action and pending_action.get("tool") == "final":
                answer = pending_action.get("answer", "")
                evidence_error = final_evidence_error(project_done, pending_action)
                if evidence_error and step < max_steps:
                    append_protocol_error(project_done, evidence_error)
                    save_project(project_done)
                    yield sse_event("tool", {"tool": "protocol", "status": "error", "summary": evidence_error})
                    continue
                project_done["messages"].append(
                    {
                        "role": "assistant",
                        "content": answer,
                        "created_at": now_iso(),
                        "thinking": "",
                    }
                )
                project_done["pending_action"] = None
                save_project(project_done)
                yield sse_event("token", {"token": answer, "raw": answer, "answer": answer, "thinking": "", "step": step})
                yield sse_event("done", project_done)
                return

            project_done["messages"].append(
                {
                    "role": "assistant",
                    "content": full_text,
                    "created_at": now_iso(),
                    "thinking": split_gemma_channels(full_text)["thinking"],
                    "hidden": bool(pending_action),
                }
            )

            if not pending_action:
                project_done["pending_action"] = None
                save_project(project_done)
                yield sse_event("done", project_done)
                return

            if not runtime.can_auto_execute(pending_action, settings):
                project_done["pending_action"] = pending_action
                save_project(project_done)
                yield sse_event("action_required", {"action": pending_action})
                yield sse_event("done", project_done)
                return

            try:
                result = runtime.execute_action(
                    pending_action,
                    unrestricted_paths=settings.get("access_mode") == "auto_all",
                )
            except Exception as exc:
                result = f"Tool error for {pending_action['tool']}: {exc}"
            runtime.append_tool_result(project_done, pending_action, result, now_iso)
            project_done["pending_action"] = None
            save_project(project_done)
            yield sse_event("tool", runtime.public_tool_event(pending_action, result))

        project_limit = load_project(project_id)
        project_limit["messages"].append(
            {
                "role": "assistant",
                "content": "Reached max tool steps for this turn.",
                "created_at": now_iso(),
            }
        )
        save_project(project_limit)
        yield sse_event("done", project_limit)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/projects/{project_id}/actions/{action_id}")
def approve_action(project_id: str, action_id: str, payload: ApproveRequest) -> dict[str, Any]:
    project = load_project(project_id)
    action = project.get("pending_action")
    if not action or action.get("id") != action_id:
        raise HTTPException(status_code=404, detail="Pending action not found")

    if payload.approved:
        result = runtime.execute_action(action, unrestricted_paths=True)
        project["messages"].append(
            {
                "role": "tool",
                "content": f"Tool result for {action['tool']}:\n{result}",
                "created_at": now_iso(),
            }
        )
    else:
        project["messages"].append(
            {"role": "tool", "content": f"User rejected action: {action['tool']}", "created_at": now_iso()}
        )
    project["pending_action"] = None
    save_project(project)
    return project
