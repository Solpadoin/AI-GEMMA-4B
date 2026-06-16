param(
  [string]$ModelPath = "",
  [string]$HfModel = "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
  [int]$Port = 8080,
  [int]$Context = 8192,
  [string]$GpuLayers = "auto",
  [int]$Parallel = 1,
  [int]$Threads = 16,
  [int]$BatchSize = 2048,
  [int]$UBatchSize = 512,
  [string]$CacheTypeK = "f16",
  [string]$CacheTypeV = "f16",
  [string]$FlashAttention = "auto",
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

$args = @(
  "--host", "127.0.0.1",
  "--port", $Port,
  "--ctx-size", $Context,
  "--n-gpu-layers", $GpuLayers,
  "--parallel", $Parallel,
  "--threads", $Threads,
  "--threads-batch", $Threads,
  "--batch-size", $BatchSize,
  "--ubatch-size", $UBatchSize,
  "--cache-type-k", $CacheTypeK,
  "--cache-type-v", $CacheTypeV,
  "--flash-attn", $FlashAttention,
  "--cache-ram", 2048
)
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
