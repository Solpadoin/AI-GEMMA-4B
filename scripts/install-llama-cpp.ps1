$ErrorActionPreference = "Stop"

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
  throw "winget is not available. Install llama.cpp manually from https://github.com/ggerganov/llama.cpp/releases"
}

winget install llama.cpp --accept-package-agreements --accept-source-agreements
Write-Host "llama.cpp installed. Open a new terminal if llama-server is not found in this session."

