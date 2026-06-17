# GEMMA-4B

Basic local agent UI for Gemma/GGUF models through `llama.cpp`.

## Current Agent Architecture

- Agent mode is enabled by default and can be switched in the UI.
- Agent turns run through a LangGraph state graph.
- The model chooses the next JSON action itself: a tool call or `final`.
- Tools do not decide what task should be done; the runtime only validates, executes, applies access policy, and records results.
- The graph rejects unsupported final answers when the required tool evidence is missing.
- Markdown answers are rendered in the chat.
- Web search, headless browser reading, context compaction, and sticky composer UI are available from the interface.
- `inspect_image` reads image metadata locally and can perform semantic image inspection only when `VISION_SERVER_URL` points to an OpenAI-compatible vision endpoint.

## Agent Gate Smoke Test

This browser smoke test opens the local UI, creates a project, and sends this exact prompt to the local model:

```text
Заккомить все изменения, предвратительно определив их, и запушь в репозиторий в этой же папке
```

The script does not run git commit or git push itself. It only verifies that the model changed `HEAD`, left the worktree clean, and pushed the branch so it is not ahead of upstream.

```powershell
.\scripts\agent-commit-push-smoke.ps1 -Repo . -Url http://127.0.0.1:7860/ -Timeout 600
```

Default model target:

```text
ggml-org/gemma-4-12B-it-GGUF:Q4_K_M
```

The old Q2 coder model is kept only as a downloaded experiment. It produced broken channel/token output on this machine, so Q4 Gemma 4 12B IT is now the preferred path.

## Quick Start

Double-click:

```text
Start-Fable5.bat
```

It will:

- create `.venv` if needed;
- install Python dependencies;
- start `llama-server` on `127.0.0.1:8080`;
- start this UI backend on `127.0.0.1:7860`;
- open the chat UI.

For a clean restart:

```text
Restart-Fable5.bat
```

To stop local services:

```text
Stop-Fable5.bat
```

There is also `Launch-Fable5.html`, but browsers cannot securely start local processes by themselves. It is only a small launcher page with links to the BAT files and UI.

## Features

- Project-based context: one project is one chat context.
- Streaming responses through Server-Sent Events.
- Gemma channel parsing: thought blocks are separated from final answers instead of dumping raw `<|channel>` text into chat.
- Thinking mode can be switched in the UI.
- Temperature can be changed in the UI from `0.0` to `1.0`.
- Access mode can be changed in the UI.
- Text file attachments can be added to a chat message.
- Workspace path guard: file actions are constrained to this repository folder.
- Basic destructive-command blocklist.

## Model Settings

Gemma 4 model card recommends:

- `temperature=1.0`
- `top_p=0.95`
- `top_k=64`

Those are the backend defaults.

Most runtime settings are controlled from the UI and stored in `data/settings.json`.

Thinking mode:

Turn on the `Thinking` switch in the sidebar.

When thinking is enabled, Gemma uses `<|think|>` in the system prompt and may emit a thought channel before the final answer. The UI hides/parses that channel.

## Access Modes

The sidebar has three access modes:

- `С подтверждением`: the model must ask before every file or command action.
- `Авто-чтение`: the model can list/read files and fetch URLs without confirmation.
- `Полный авто-доступ`: the model can write files and run commands without confirmation.

In confirmation mode, clicking approve allows that exact action to access paths outside the repository. In full auto mode, filesystem paths are unrestricted. A small destructive-command blocklist remains active. This is not a VM sandbox.

Current supported agent actions:

- `list_files`
- `read_file`
- `write_file`
- `run_command`
- `fetch_url`
- `open_url`

Browser access is intentionally minimal. The local agent can fetch web pages by URL and open URLs in the default browser. It does not yet control browser tabs like Codex does with its in-app Browser plugin.

## Attachments

Use the `+` button in the composer to attach local text/code files. The browser reads the selected files and sends their text content in the chat request, so the model can summarize, inspect, or transform them.

## Manual Commands

Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start model server:

```powershell
.\scripts\start-llama-server.ps1
```

The default server profile is tuned for the tested machine:

- `--n-gpu-layers auto`
- `--parallel 1`
- `--cache-type-k f16`
- `--cache-type-v f16`
- `--flash-attn auto`
- `--batch-size 2048`
- `--ubatch-size 512`
- `--threads 16`

On the tested RTX 5070 Laptop GPU, this roughly doubled generation speed compared with the first CPU-heavy profile. If your GPU has less VRAM, change `CacheTypeK/CacheTypeV` to `q8_0` or `q4_0`, or set fewer GPU layers.

Start backend:

```powershell
.\scripts\start-backend.ps1
```

Open:

```text
http://127.0.0.1:7860/
```

## Safety Notes

The model cannot directly execute commands or write files. It can only propose an `agent_action`, and the UI asks for confirmation.

This is a basic guardrail, not a full VM sandbox. For serious autonomous coding work, add command allowlists, diff previews before writes, and a separate sandbox process.
