from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "").rstrip("/")
DEFAULT_SETTINGS = {
    "model_name": os.getenv("MODEL_NAME", "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M"),
    "thinking": os.getenv("ENABLE_THINKING", "0").lower() in {"1", "true", "yes", "on"},
    "temperature": float(os.getenv("TEMPERATURE", "1.0")),
    "top_p": float(os.getenv("TOP_P", "0.95")),
    "top_k": int(os.getenv("TOP_K", "64")),
    "max_tokens": int(os.getenv("MAX_TOKENS", "512")),
    "access_mode": os.getenv("ACCESS_MODE", "confirm"),
    "max_tool_steps": int(os.getenv("MAX_TOOL_STEPS", "4")),
}

SYSTEM_PROMPT = """You are a careful local coding assistant.
You can discuss, edit, and inspect files only through approved actions.
Never claim you executed a command or changed a file unless a tool result says so.
If you need a tool, end your answer with exactly one fenced block:
```agent_action
{"tool":"read_file","path":"relative/path.txt"}
```
Supported tools: list_files, read_file, write_file, run_command, fetch_url, open_url.
write_file requires path and content. run_command requires command. fetch_url and open_url require url.
Prefer small, reversible actions and explain why the user should approve them."""

DESTRUCTIVE_COMMANDS = (
    "Remove-Item",
    "rm ",
    "rmdir",
    "del ",
    "erase ",
    "format ",
    "git reset",
    "git clean",
    "shutdown",
    "Stop-Computer",
)

DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Fable5 Local Agent")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_llm: Any | None = None


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20000)
    attachments: list[dict[str, str]] = Field(default_factory=list)


class ApproveRequest(BaseModel):
    approved: bool


class SettingsUpdate(BaseModel):
    thinking: bool | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=1.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1, le=200)
    max_tokens: int | None = Field(default=None, ge=16, le=4096)
    access_mode: Literal["confirm", "auto_read", "auto_all"] | None = None
    max_tool_steps: int | None = Field(default=None, ge=0, le=12)


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
    if settings.get("thinking"):
        return "<|think|>\n" + SYSTEM_PROMPT
    return SYSTEM_PROMPT


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
    return {
        "id": project["id"],
        "name": project["name"],
        "created_at": project["created_at"],
        "updated_at": project["updated_at"],
        "messages": len(project.get("messages", [])),
        "pending_action": project.get("pending_action"),
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


def extract_action(text: str) -> dict[str, Any] | None:
    match = re.search(r"```agent_action\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        action = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if action.get("tool") not in {"list_files", "read_file", "write_file", "run_command", "fetch_url", "open_url"}:
        return None
    action["id"] = str(uuid.uuid4())
    action["created_at"] = now_iso()
    return action


def safe_path(raw_path: str | None) -> Path:
    if not raw_path:
        raise HTTPException(status_code=400, detail="Missing path")
    candidate = (WORKSPACE_ROOT / raw_path).resolve()
    if WORKSPACE_ROOT not in candidate.parents and candidate != WORKSPACE_ROOT:
        raise HTTPException(status_code=400, detail="Path escapes workspace")
    return candidate


def execute_action(action: dict[str, Any]) -> str:
    tool = action.get("tool")
    if tool == "list_files":
        root = safe_path(action.get("path", "."))
        files = [str(path.relative_to(WORKSPACE_ROOT)) for path in root.rglob("*") if path.is_file()]
        return "\n".join(files[:300]) or "(no files)"

    if tool == "read_file":
        path = safe_path(action.get("path"))
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return path.read_text(encoding="utf-8", errors="replace")[:40000]

    if tool == "write_file":
        path = safe_path(action.get("path"))
        content = action.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="Missing content")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"Wrote {path.relative_to(WORKSPACE_ROOT)}"

    if tool == "run_command":
        command = str(action.get("command", "")).strip()
        if not command:
            raise HTTPException(status_code=400, detail="Missing command")
        lowered = command.lower()
        if any(item.lower() in lowered for item in DESTRUCTIVE_COMMANDS):
            raise HTTPException(status_code=400, detail="Command blocked as destructive")
        if os.name == "nt":
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]
        else:
            args = shlex.split(command)
        completed = subprocess.run(args, cwd=WORKSPACE_ROOT, text=True, capture_output=True, timeout=60)
        output = completed.stdout + completed.stderr
        return output[:40000] or f"Command exited with {completed.returncode}"

    if tool == "fetch_url":
        url = str(action.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="Only http(s) URLs are supported")
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        return response.text[:40000]

    if tool == "open_url":
        url = str(action.get("url", "")).strip()
        if not url.startswith(("http://", "https://", "file://")):
            raise HTTPException(status_code=400, detail="Only http(s) and file URLs are supported")
        if os.name == "nt":
            subprocess.Popen(["powershell", "-NoProfile", "-Command", "Start-Process", url], cwd=WORKSPACE_ROOT)
        else:
            subprocess.Popen(["xdg-open", url], cwd=WORKSPACE_ROOT)
        return f"Opened {url}"

    raise HTTPException(status_code=400, detail="Unknown tool")


def can_auto_execute(action: dict[str, Any], settings: dict[str, Any]) -> bool:
    mode = settings.get("access_mode", "confirm")
    if mode == "auto_all":
        return True
    if mode == "auto_read":
        return action.get("tool") in {"list_files", "read_file", "fetch_url"}
    return False


def build_messages(project: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt(settings)}]
    messages.extend({"role": msg["role"], "content": msg["content"]} for msg in project["messages"][-40:])
    return messages


def run_agent_turn(project: dict[str, Any], settings: dict[str, Any]) -> str:
    final_text = ""
    project["pending_action"] = None
    max_steps = int(settings.get("max_tool_steps", 4))
    for step in range(max_steps + 1):
        assistant_text = create_chat_completion(build_messages(project, settings), settings)
        final_text = assistant_text
        action = extract_action(assistant_text)
        if not action:
            project["messages"].append({"role": "assistant", "content": assistant_text, "created_at": now_iso()})
            project["pending_action"] = None
            return final_text

        project["messages"].append({"role": "assistant", "content": assistant_text, "created_at": now_iso()})
        if not can_auto_execute(action, settings):
            project["pending_action"] = action
            return final_text

        try:
            result = execute_action(action)
        except Exception as exc:
            result = f"Tool error for {action['tool']}: {exc}"
        project["messages"].append(
            {
                "role": "tool",
                "content": f"Tool result for {action['tool']}:\n{result}",
                "created_at": now_iso(),
            }
        )

    project["messages"].append(
        {
            "role": "assistant",
            "content": "Reached max tool steps for this turn.",
            "created_at": now_iso(),
        }
    )
    return final_text


def format_user_message(message: str, attachments: list[dict[str, str]]) -> str:
    if not attachments:
        return message
    parts = [message, "\n\nAttached files:"]
    for item in attachments:
        name = item.get("name", "attachment")
        content = item.get("content", "")
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

    messages = build_messages(project, settings)

    def generate():
        full_text = ""
        yield sse_event("start", {"project_id": project_id})
        try:
            for token in stream_chat_completion(messages, settings):
                full_text += token
                parsed = split_gemma_channels(full_text)
                yield sse_event(
                    "token",
                    {
                        "token": token,
                        "raw": full_text,
                        "answer": parsed["answer"],
                        "thinking": parsed["thinking"],
                    },
                )
        except Exception as exc:
            yield sse_event("error", {"detail": str(exc)})
            return

        pending_action = extract_action(full_text)
        project_done = load_project(project_id)
        project_done["messages"].append(
            {
                "role": "assistant",
                "content": full_text,
                "created_at": now_iso(),
                "thinking": split_gemma_channels(full_text)["thinking"],
            }
        )
        if pending_action and can_auto_execute(pending_action, settings):
            try:
                result = execute_action(pending_action)
            except Exception as exc:
                result = f"Tool error for {pending_action['tool']}: {exc}"
            project_done["messages"].append(
                {
                    "role": "tool",
                    "content": f"Tool result for {pending_action['tool']}:\n{result}",
                    "created_at": now_iso(),
                }
            )
            project_done["pending_action"] = None
            yield sse_event("tool", {"action": pending_action, "result": result})
        else:
            project_done["pending_action"] = pending_action
        save_project(project_done)
        yield sse_event("done", project_done)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/projects/{project_id}/actions/{action_id}")
def approve_action(project_id: str, action_id: str, payload: ApproveRequest) -> dict[str, Any]:
    project = load_project(project_id)
    action = project.get("pending_action")
    if not action or action.get("id") != action_id:
        raise HTTPException(status_code=404, detail="Pending action not found")

    if payload.approved:
        result = execute_action(action)
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
