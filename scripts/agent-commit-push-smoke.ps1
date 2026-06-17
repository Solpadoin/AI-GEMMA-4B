param(
  [string]$Repo = ".",
  [string]$Url = "http://127.0.0.1:7860/",
  [int]$Timeout = 600,
  [switch]$Headed
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

$argsList = @(
  (Join-Path $PSScriptRoot "agent-commit-push-smoke.py"),
  "--repo", $Repo,
  "--url", $Url,
  "--timeout", [string]$Timeout
)

if ($Headed) {
  $argsList += "--headed"
}

& $python @argsList
