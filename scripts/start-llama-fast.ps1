param(
  [int]$Port = 8080
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\start-llama-server.ps1" `
  -Port $Port `
  -Context 4096 `
  -GpuLayers "999" `
  -Parallel 1 `
  -Threads 12 `
  -BatchSize 1024 `
  -UBatchSize 256 `
  -CacheTypeK "q8_0" `
  -CacheTypeV "q8_0" `
  -FlashAttention "on"
