# Goose runtime path

This project should not grow a custom agent runtime forever. The preferred real-agent path is Goose, using Ollama or the local `llama-server` as the model runtime.

## Why Goose

Goose is a native open-source AI agent with Desktop, CLI, API, filesystem/command execution, and MCP extensions. It is built for the same class of work as local coding/OS agents instead of being a simple chat UI.

Useful official references:

- Install goose: https://goose-docs.ai/docs/getting-started/installation/
- Providers: https://goose-docs.ai/docs/getting-started/providers/
- Repository: https://github.com/aaif-goose/goose

## Hard constraint

Goose relies heavily on model tool calling. A local model that does not reliably emit tool calls can still hallucinate, even inside Goose. If Gemma 4 12B IT keeps failing tool use, switch the local model to a stronger tool-calling model served through the same OpenAI-compatible endpoint.

Good candidates to test through `llama-server`, Ollama, LM Studio, or another OpenAI-compatible server:

- Qwen coder/instruct tool-calling variants.
- Llama instruct/tool-use variants.
- Mistral tool-use variants.

## Install Goose CLI on Windows

```powershell
.\scripts\install-goose.ps1 -NoConfigure
```

If the command is not visible in a new terminal, add this to the PowerShell profile:

```powershell
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
```

## Start local model server

```powershell
.\scripts\start-llama-server.ps1
```

The default local OpenAI-compatible server is:

```text
http://127.0.0.1:8080/v1
```

## Recommended local agent model

For this RTX 5070 Laptop / 32 GB RAM machine, the practical agent profile is:

```text
Runtime: Ollama 0.30+
Model: qwen3-coder:30b
Architecture: MoE, 30B total / 3.3B active
```

Ollama lists this model as a coding/agentic model with tool support and a 256K context window. On this machine, a hot local benchmark at `num_ctx=4096` measured about `30 tok/s` decode with Ollama reporting `68%/32% CPU/GPU`.

Install and pull:

```powershell
winget install Ollama.Ollama
$env:PATH = "$env:LOCALAPPDATA\Programs\Ollama;$env:PATH"
ollama pull qwen3-coder:30b
.\scripts\benchmark-ollama.ps1
```

Run Goose with Ollama:

```powershell
.\scripts\start-goose-ollama.ps1 -Workspace "C:\Users\Admin\Documents\ZM 4.3\CS1.6-ZM-WEBSITE"
```

This sets:

```text
GOOSE_PROVIDER=ollama
GOOSE_MODEL=qwen3-coder:30b
OLLAMA_HOST=http://127.0.0.1:11434
```

## Alternative: Goose against llama-server

From the repo you want Goose to work on:

```powershell
.\scripts\start-goose-local.ps1 -Workspace "C:\Users\Admin\Documents\ZM 4.3\CS1.6-ZM-WEBSITE"
```

This sets:

```text
GOOSE_PROVIDER=openai
GOOSE_MODEL=ggml-org/gemma-4-12B-it-GGUF:Q4_K_M
OPENAI_HOST=http://127.0.0.1:8080
OPENAI_API_KEY=local
```

Then Goose, not this FastAPI prototype, owns the agent loop.

## Expected migration

1. Keep this repo as launcher/configuration around local models.
2. Use Goose for OS/files/browser/MCP-style agent execution.
3. Replace the current FastAPI chat loop with either:
   - a thin Goose launcher/status UI, or
   - no custom UI at all, using Goose Desktop/CLI directly.
4. Keep the current UI only for model-server control and diagnostics if it remains useful.
