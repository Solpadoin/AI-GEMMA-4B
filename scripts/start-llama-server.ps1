param(
  [string]$ModelPath = "",
  [string]$HfModel = "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
  [int]$Port = 8080,
  [int]$Context = 8192,
  [int]$GpuLayers = 0,
  [string]$ChatTemplate = "",
  [switch]$Thinking
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DefaultLocalModel = Join-Path $Root "models\gemma-4-12B-it-Q4_K_M.gguf"

$LlamaServer = (Get-Command llama-server -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source)
if (-not $LlamaServer) {
  $LlamaServer = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Recurse -Filter llama-server.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
}

if (-not $LlamaServer) {
  throw "llama-server not found. Run .\scripts\install-llama-cpp.ps1 first."
}

$args = @("--host", "127.0.0.1", "--port", $Port, "--ctx-size", $Context, "--n-gpu-layers", $GpuLayers)
if ($ChatTemplate) {
  $args += @("--chat-template", $ChatTemplate)
}
if ($Thinking) {
  $args += @("--reasoning", "on")
} else {
  $args += @("--reasoning", "off")
}

if (-not $ModelPath -and (Test-Path $DefaultLocalModel)) {
  $ModelPath = $DefaultLocalModel
}

if ($ModelPath) {
  $args = @("--model", $ModelPath) + $args
} else {
  $args = @("--hf-repo", $HfModel) + $args
}

& $LlamaServer @args
