param(
  [string]$ModelName = "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
  [string]$LlamaServerUrl = "http://127.0.0.1:8080",
  [int]$Port = 7860,
  [int]$MaxTokens = 512,
  [switch]$Thinking
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

$env:LLAMA_SERVER_URL = $LlamaServerUrl
$env:MODEL_NAME = $ModelName
$env:MAX_TOKENS = "$MaxTokens"
$env:ENABLE_THINKING = if ($Thinking) { "1" } else { "0" }
$env:TEMPERATURE = "1.0"
$env:TOP_P = "0.95"
$env:TOP_K = "64"

Set-Location $Root
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port $Port
