param(
  [string]$Workspace = (Get-Location).Path,
  [string]$Model = "ggml-org/gemma-4-12B-it-GGUF:Q4_K_M",
  [string]$OpenAIHost = "http://127.0.0.1:8080",
  [string]$ApiKey = "local"
)

$ErrorActionPreference = "Stop"
$goose = Get-Command goose -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $goose) {
  $candidate = Join-Path $env:USERPROFILE ".local\bin\goose.exe"
  if (Test-Path $candidate) {
    $goose = $candidate
  }
}

if (-not $goose) {
  throw "goose CLI not found. Run .\scripts\install-goose.ps1 first, then open a new terminal."
}

$env:GOOSE_PROVIDER = "openai"
$env:GOOSE_MODEL = $Model
$env:OPENAI_HOST = $OpenAIHost
$env:OPENAI_API_KEY = $ApiKey

Push-Location $Workspace
try {
  & $goose session
}
finally {
  Pop-Location
}
