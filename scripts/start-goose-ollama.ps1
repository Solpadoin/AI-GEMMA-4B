param(
  [string]$Workspace = (Get-Location).Path,
  [string]$Model = "qwen3-coder:30b",
  [string]$OllamaHost = "http://127.0.0.1:11434"
)

$ErrorActionPreference = "Stop"
$ollama = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $ollama) {
  $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $candidate) {
    $ollama = $candidate
  }
}

if (-not $ollama) {
  throw "Ollama not found. Install it with: winget install Ollama.Ollama"
}

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

try {
  Invoke-RestMethod -Uri "$OllamaHost/api/tags" -TimeoutSec 2 | Out-Null
} catch {
  Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
  Start-Sleep -Seconds 5
}

& $ollama pull $Model

$env:GOOSE_PROVIDER = "ollama"
$env:GOOSE_MODEL = $Model
$env:OLLAMA_HOST = $OllamaHost

Push-Location $Workspace
try {
  & $goose session
}
finally {
  Pop-Location
}
