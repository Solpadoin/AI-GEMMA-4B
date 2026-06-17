param(
  [string]$Source = "https://huggingface.co/ggml-org/gemma-4-12B-it-GGUF/resolve/main/gemma-4-12B-it-Q4_K_M.gguf",
  [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Destination) {
  $Destination = Join-Path $Root "models\gemma-4-12B-it-Q4_K_M.gguf"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
Import-Module BitsTransfer

$existing = Get-BitsTransfer | Where-Object { $_.DisplayName -eq "Gemma Local Q4" } | Select-Object -First 1
if ($existing) {
  Write-Host "Existing BITS job:"
  $existing | Select-Object DisplayName, JobState, BytesTransferred, BytesTotal
  return
}

Start-BitsTransfer -Source $Source -Destination $Destination -DisplayName "Gemma Local Q4" -Asynchronous |
  Select-Object DisplayName, JobState, BytesTransferred, BytesTotal
