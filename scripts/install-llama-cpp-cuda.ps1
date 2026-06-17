param(
  [string]$Version = "latest",
  [ValidateSet("12.4", "13.3")]
  [string]$Cuda = "12.4"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tools = Join-Path $root "tools"
$installDir = Join-Path $tools "llama-cpp-cuda"
$downloadDir = Join-Path $tools "downloads"
New-Item -ItemType Directory -Force -Path $installDir, $downloadDir | Out-Null

if ($Version -eq "latest") {
  $release = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest" `
    -Headers @{ "User-Agent" = "Gemma-Agent" }
} else {
  $release = Invoke-RestMethod `
    -Uri "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$Version" `
    -Headers @{ "User-Agent" = "Gemma-Agent" }
}

$suffix = if ($Cuda -eq "13.3") { "cuda-13.3-x64.zip" } else { "cuda-12.4-x64.zip" }
$binAsset = $release.assets | Where-Object { $_.name -like "llama-*-bin-win-$suffix" } | Select-Object -First 1
$runtimeAsset = $release.assets | Where-Object { $_.name -eq "cudart-llama-bin-win-$suffix" } | Select-Object -First 1

if (-not $binAsset -or -not $runtimeAsset) {
  throw "Could not find Windows CUDA $Cuda assets in llama.cpp release $($release.tag_name)."
}

$binZip = Join-Path $downloadDir $binAsset.name
$runtimeZip = Join-Path $downloadDir $runtimeAsset.name

function Download-Asset {
  param($Asset, [string]$Destination)

  Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
  Write-Host "Downloading $($Asset.name)"
  Invoke-WebRequest -Uri $Asset.browser_download_url -OutFile $Destination
  $item = Get-Item -LiteralPath $Destination
  if ($item.Length -ne [int64]$Asset.size) {
    Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
    throw "Download size mismatch for $($Asset.name): got $($item.Length), expected $($Asset.size). Re-run the installer."
  }
}

Download-Asset $binAsset $binZip
Download-Asset $runtimeAsset $runtimeZip

if (Test-Path $installDir) {
  Get-ChildItem -LiteralPath $installDir -Force | Remove-Item -Recurse -Force
}

Expand-Archive -Path $binZip -DestinationPath $installDir -Force
Expand-Archive -Path $runtimeZip -DestinationPath $installDir -Force

$server = Get-ChildItem -LiteralPath $installDir -Recurse -Filter llama-server.exe | Select-Object -First 1
if (-not $server) {
  throw "llama-server.exe was not found after extraction."
}

Write-Host "Installed llama.cpp $($release.tag_name) CUDA $Cuda to $installDir"
Write-Host "Server: $($server.FullName)"
