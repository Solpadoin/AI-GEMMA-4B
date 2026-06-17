from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import uuid
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError


ToolName = Literal[
    "list_files",
    "search_in_files",
    "read_file",
    "write_file",
    "replace_in_file",
    "run_command",
    "search_web",
    "fetch_url",
    "browser_read",
    "inspect_image",
    "open_url",
    "final",
]


class AgentAction(BaseModel):
    tool: ToolName
    path: str | None = None
    query: str | None = None
    content: str | None = None
    old: str | None = None
    new: str | None = None
    command: str | None = None
    cwd: str | None = None
    url: str | None = None
    prompt: str | None = None
    answer: str | None = None


class ToolEvent(BaseModel):
    tool: str
    status: Literal["ok", "error"]
    summary: str


@dataclass
class AgentRuntime:
    workspace_root: Path
    load_settings: Callable[[], dict[str, Any]]

    def parse_action(self, text: str) -> dict[str, Any] | None:
        candidates = re.findall(r"```(?:agent_action|json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        if not candidates:
            candidates = [text.strip()]

        parsed: AgentAction | None = None
        for raw in candidates:
            try:
                candidate = AgentAction.model_validate_json(raw)
            except (ValidationError, ValueError):
                continue
            parsed = candidate

        if parsed is None:
            return None

        action = parsed.model_dump(exclude_none=True)
        action["id"] = str(uuid.uuid4())
        return action

    def needs_agent_mode(self, project: dict[str, Any]) -> bool:
        latest = self.latest_user_text(project).lower()
        if not latest:
            return False
        if re.search(r"https?://\S+", latest):
            return True
        has_path = bool(re.search(r"[a-z]:\\", latest, flags=re.IGNORECASE))
        action_words = (
            "измени",
            "изменить",
            "модифиц",
            "замени",
            "заменить",
            "сделай",
            "запуш",
            "push",
            "commit",
            "коммит",
            "replace",
            "change",
            "modify",
            "edit",
        )
        return has_path and any(word in latest for word in action_words)

    @staticmethod
    def latest_user_text(project: dict[str, Any]) -> str:
        for message in reversed(project.get("messages", [])):
            if message.get("role") == "user":
                return str(message.get("content", ""))
        return ""

    @staticmethod
    def tool_messages_since_latest_user(project: dict[str, Any]) -> list[dict[str, Any]]:
        messages = []
        for message in reversed(project.get("messages", [])):
            if message.get("role") == "user":
                return list(reversed(messages))
            if message.get("role") == "tool" and str(message.get("content", "")).startswith("Tool result for"):
                messages.append(message)
        return list(reversed(messages))

    def has_tool_result_since_latest_user(self, project: dict[str, Any]) -> bool:
        return bool(self.tool_messages_since_latest_user(project))

    def task_requirements_satisfied(self, project: dict[str, Any]) -> bool:
        latest = self.latest_user_text(project).lower()
        if not latest:
            return True
        tools = self.tool_messages_since_latest_user(project)
        if not tools:
            return False

        actions = [msg.get("action") or {} for msg in tools]
        contents = "\n".join(str(msg.get("content", "")) for msg in tools).lower()

        if re.search(r"https?://\S+", latest):
            return any(action.get("tool") in {"browser_read", "fetch_url", "search_web"} for action in actions)

        asks_edit = any(word in latest for word in ("измени", "изменить", "замени", "заменить", "replace", "change", "modify", "edit"))
        asks_commit = any(word in latest for word in ("commit", "коммит"))
        asks_push = any(word in latest for word in ("push", "запуш"))

        if asks_edit and not any(action.get("tool") in {"replace_in_file", "write_file"} for action in actions):
            return False
        if asks_commit and not any(action.get("tool") == "run_command" and "git commit" in str(action.get("command", "")).lower() for action in actions):
            return False
        if asks_push and not any(action.get("tool") == "run_command" and "git push" in str(action.get("command", "")).lower() for action in actions):
            return False
        if "tool error" in contents:
            return False
        return True

    def build_action_messages(self, project: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, str]]:
        prompt = ACTION_SELECTION_PROMPT
        prompt += "\nRuntime capabilities:\n" + json.dumps(self.capabilities(), ensure_ascii=False)
        if settings.get("web_search", True):
            prompt += "\nWeb search is enabled."
        else:
            prompt += "\nWeb search is disabled. Do not call search_web."

        messages = [{"role": "system", "content": prompt}]
        messages.extend(
            {"role": msg["role"], "content": msg["content"]}
            for msg in project.get("messages", [])[-20:]
            if msg.get("role") in {"user", "assistant", "tool"}
        )
        return messages

    def safe_path(self, raw_path: str | None, unrestricted: bool = False) -> Path:
        if not raw_path:
            raise HTTPException(status_code=400, detail="Missing path")
        raw = Path(raw_path)
        candidate = (raw if raw.is_absolute() else self.workspace_root / raw).resolve()
        if not unrestricted and self.workspace_root not in candidate.parents and candidate != self.workspace_root:
            raise HTTPException(status_code=400, detail="Path escapes workspace")
        return candidate

    def can_auto_execute(self, action: dict[str, Any], settings: dict[str, Any]) -> bool:
        mode = settings.get("access_mode", "confirm")
        if mode == "auto_all":
            return True
        if mode == "auto_read":
            return action.get("tool") in {"list_files", "search_in_files", "read_file", "fetch_url", "browser_read", "search_web"}
        return False

    def execute_action(self, action: dict[str, Any], unrestricted_paths: bool = False) -> str:
        tool = action.get("tool")
        if tool == "final":
            return action.get("answer", "")

        if tool == "list_files":
            root = self.safe_path(action.get("path", "."), unrestricted_paths)
            files = []
            for path in root.rglob("*"):
                if path.is_file():
                    try:
                        files.append(str(path.relative_to(self.workspace_root)))
                    except ValueError:
                        files.append(str(path))
            return "\n".join(files[:300]) or "(no files)"

        if tool == "search_in_files":
            root = self.safe_path(action.get("path", "."), unrestricted_paths)
            query = str(action.get("query", "")).strip()
            if not query:
                raise HTTPException(status_code=400, detail="Missing query")
            if not root.exists():
                raise HTTPException(status_code=404, detail="Path not found")
            return search_files(root, query)

        if tool == "read_file":
            path = self.safe_path(action.get("path"), unrestricted_paths)
            if not path.exists() or not path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            return path.read_text(encoding="utf-8", errors="replace")[:40000]

        if tool == "write_file":
            path = self.safe_path(action.get("path"), unrestricted_paths)
            content = action.get("content")
            if not isinstance(content, str):
                raise HTTPException(status_code=400, detail="Missing content")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Wrote {display_path(path, self.workspace_root)}"

        if tool == "replace_in_file":
            path = self.safe_path(action.get("path"), unrestricted_paths)
            old = action.get("old")
            new = action.get("new")
            if not isinstance(old, str) or not isinstance(new, str) or old == "":
                raise HTTPException(status_code=400, detail="Missing old/new replacement text")
            if not path.exists() or not path.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            content = path.read_text(encoding="utf-8", errors="replace")
            count = content.count(old)
            if count == 0:
                raise HTTPException(status_code=400, detail="Replacement text was not found")
            path.write_text(content.replace(old, new), encoding="utf-8")
            return f"Replaced {count} occurrence(s) in {display_path(path, self.workspace_root)}"

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
            cwd = self.safe_path(action.get("cwd"), unrestricted_paths) if action.get("cwd") else self.workspace_root
            completed = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=60)
            output = completed.stdout + completed.stderr
            return output[:40000] or f"Command exited with {completed.returncode}"

        if tool == "fetch_url":
            url = str(action.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="Only http(s) URLs are supported")
            response = httpx.get(url, timeout=30, follow_redirects=True)
            response.raise_for_status()
            return response.text[:40000]

        if tool == "browser_read":
            url = str(action.get("url", "")).strip()
            if not url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="Only http(s) URLs are supported")
            return browser_read(url)

        if tool == "search_web":
            if not self.load_settings().get("web_search", True):
                raise HTTPException(status_code=400, detail="Web search is disabled in settings")
            query = str(action.get("query", "")).strip()
            if not query:
                raise HTTPException(status_code=400, detail="Missing query")
            return search_web(query)

        if tool == "inspect_image":
            path = self.safe_path(action.get("path"), unrestricted_paths)
            prompt = str(action.get("prompt") or "Describe this image.")
            return inspect_image(path, prompt)

        if tool == "open_url":
            url = str(action.get("url", "")).strip()
            if not url.startswith(("http://", "https://", "file://")):
                raise HTTPException(status_code=400, detail="Only http(s) and file URLs are supported")
            if os.name == "nt":
                subprocess.Popen(["powershell", "-NoProfile", "-Command", "Start-Process", url], cwd=self.workspace_root)
            else:
                subprocess.Popen(["xdg-open", url], cwd=self.workspace_root)
            return f"Opened {url}"

        raise HTTPException(status_code=400, detail="Unknown tool")

    @staticmethod
    def append_tool_result(project: dict[str, Any], action: dict[str, Any], result: str, now_iso: Callable[[], str]) -> None:
        project["messages"].append(
            {
                "role": "tool",
                "content": f"Tool result for {action['tool']}:\n{result}",
                "created_at": now_iso(),
                "hidden": True,
                "tool": action.get("tool"),
                "action": action,
            }
        )

    @staticmethod
    def public_tool_event(action: dict[str, Any], result: str) -> dict[str, str]:
        text = str(result or "")
        status = "error" if text.lower().startswith("tool error") else "ok"
        preview = re.sub(r"\s+", " ", text).strip()[:240]
        return ToolEvent(tool=str(action.get("tool", "tool")), status=status, summary=preview or "done").model_dump()

    def capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "text_files": {"enabled": True, "tools": ["list_files", "search_in_files", "read_file", "write_file", "replace_in_file"]},
            "shell": {"enabled": True, "tools": ["run_command"]},
            "web_search": {"enabled": bool(self.load_settings().get("web_search", True)), "tools": ["search_web"]},
            "image_metadata": {"enabled": has_module("PIL"), "tools": ["inspect_image"]},
            "vision": {
                "enabled": bool(os.getenv("VISION_SERVER_URL", "").strip()),
                "tools": ["inspect_image"],
                "detail": "Requires VISION_SERVER_URL pointing to an OpenAI-compatible vision or llama.cpp multimodal server.",
            },
            "browser": {"enabled": has_module("playwright"), "tools": ["browser_read"]},
        }
        return caps


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def search_files(root: Path, query: str) -> str:
    matches = []
    query_lower = query.lower()
    files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
    ignored_parts = {".git", "node_modules", "__pycache__", ".venv"}
    for path in files:
        if ignored_parts.intersection(path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if query_lower in line.lower():
                matches.append(f"{path}:{lineno}: {line.strip()[:240]}")
                if len(matches) >= 80:
                    return "\n".join(matches)
    return "\n".join(matches) or "No matches found."


def search_web(query: str) -> str:
    response = httpx.get(
        "https://duckduckgo.com/html/",
        params={"q": query},
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 Gemma Local Agent"},
    )
    response.raise_for_status()
    html = response.text
    rows = re.findall(
        r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
        html,
        flags=re.DOTALL,
    )
    if not rows:
        rows = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, flags=re.DOTALL)
    results = []
    for row in rows[:8]:
        url, title = row[0], row[1]
        snippet = row[2] if len(row) > 2 else ""
        clean = lambda value: re.sub(r"\s+", " ", re.sub(r"<.*?>", "", value)).strip()
        results.append(f"- {clean(title)}\n  {url}\n  {clean(snippet)}")
    return "\n".join(results) or "No search results found."


def browser_read(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Playwright is not installed: {exc}") from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = page.title()
            final_url = page.url
            text = page.locator("body").inner_text(timeout=10000)
        finally:
            browser.close()
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"URL: {final_url}\nTitle: {title}\n\n{text[:30000]}"


def inspect_image(path: Path, prompt: str) -> str:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        from PIL import Image
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Pillow is not installed: {exc}") from exc

    with Image.open(path) as image:
        width, height = image.size
        mode = image.mode
        fmt = image.format or path.suffix.lstrip(".").upper()

    vision_url = os.getenv("VISION_SERVER_URL", "").rstrip("/")
    if not vision_url:
        return (
            f"Image metadata only: {path}\n"
            f"Format: {fmt}\nSize: {width}x{height}\nMode: {mode}\n\n"
            "No VISION_SERVER_URL is configured, so semantic image understanding is disabled. "
            "Configure an OpenAI-compatible vision server or llama.cpp multimodal server with a vision model/mmproj."
        )

    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "model": os.getenv("VISION_MODEL", "vision"),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ],
            }
        ],
        "max_tokens": 800,
    }
    response = httpx.post(f"{vision_url}/v1/chat/completions", json=payload, timeout=120)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


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


ACTION_SELECTION_PROMPT = """You are a local Windows AI agent controller.
Read the full conversation context and choose exactly one next step.
You may call a tool, or you may finish with final if no tool is needed.
Tools do not decide what to do. You decide when and why to use them.

Return a single JSON object, no prose and no markdown:
{"tool":"final","answer":"brief answer"}

Only call final after tool results prove the work is done. Never claim that you read a website, committed, pushed, edited, or inspected a local path unless the relevant tool result is already in context.

Valid tools:
- list_files: {"tool":"list_files","path":"C:\\path\\to\\folder"}
- search_in_files: {"tool":"search_in_files","path":"C:\\path\\to\\folder","query":"text"}
- read_file: {"tool":"read_file","path":"C:\\path\\to\\file"}
- replace_in_file: {"tool":"replace_in_file","path":"C:\\path\\to\\file","old":"text","new":"text"}
- write_file: {"tool":"write_file","path":"C:\\path\\to\\file","content":"text"}
- run_command: {"tool":"run_command","command":"PowerShell command","cwd":"C:\\path\\to\\repo"}
- search_web: {"tool":"search_web","query":"query"}
- fetch_url: {"tool":"fetch_url","url":"https://example.com"}
- browser_read: {"tool":"browser_read","url":"https://example.com"}
- inspect_image: {"tool":"inspect_image","path":"C:\\path\\to\\image.png","prompt":"what should be inspected"}
- open_url: {"tool":"open_url","url":"https://example.com"}
- final: {"tool":"final","answer":"brief final answer after the work is complete"}

Use browser_read for questions about a specific website URL.
For repository edits, use cwd for the requested repository path. Search/read first, edit if needed, verify with git diff/status, commit/push if requested, then final.
For questions about a website URL, use browser_read first and base the answer only on its result.
If the user asks to inspect an image, use inspect_image when an image path is available.
Never say you cannot access C:\\ paths. Use a tool."""
