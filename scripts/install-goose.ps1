param(
  [switch]$NoConfigure
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$installer = Join-Path $root "download_goose_cli.ps1"

Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/aaif-goose/goose/main/download_cli.ps1" `
  -OutFile $installer

if ($NoConfigure) {
  $env:CONFIGURE = "false"
}

& powershell -NoProfile -ExecutionPolicy Bypass -File $installer

$localBin = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path $localBin) {
  $env:PATH = "$localBin;$env:PATH"
}

Write-Host ""
Write-Host "Goose CLI install finished."
Write-Host "If 'goose' is not found in a new terminal, add this to your PowerShell profile:"
Write-Host '$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"'
