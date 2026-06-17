param(
  [int]$Port = 8080,
  [string]$HfModel = "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:UD-Q4_K_XL"
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\start-llama-server.ps1" `
  -Port $Port `
  -HfModel $HfModel `
  -Context 8192 `
  -GpuLayers "999" `
  -Parallel 1 `
  -Threads 16 `
  -BatchSize 1024 `
  -UBatchSize 256 `
  -CacheTypeK "q8_0" `
  -CacheTypeV "q8_0" `
  -FlashAttention "on"
