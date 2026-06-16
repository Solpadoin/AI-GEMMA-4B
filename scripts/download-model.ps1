param(
  [string]$RepoId = "yuxinlu1/gemma-4-12B-coder-fable5-composer2.5-v1-GGUF",
  [string]$Filename = "gemma4-coding-Q2_K.gguf"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ModelDir = Join-Path $Root "models"
New-Item -ItemType Directory -Force -Path $ModelDir | Out-Null

python -m pip install --upgrade huggingface-hub
hf download $RepoId $Filename --local-dir $ModelDir

Write-Host "Model downloaded to $ModelDir\$Filename"
