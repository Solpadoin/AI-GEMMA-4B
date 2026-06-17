param(
  [string]$Model = "qwen3-coder:30b",
  [int]$Context = 4096,
  [int]$Predict = 160
)

$ErrorActionPreference = "Stop"
$body = @{
  model = $Model
  prompt = "Return a compact bullet list of five git commands and their purpose."
  stream = $false
  options = @{
    num_ctx = $Context
    num_predict = $Predict
    temperature = 0.2
  }
} | ConvertTo-Json -Depth 5

$response = Invoke-RestMethod `
  -Uri "http://127.0.0.1:11434/api/generate" `
  -Method Post `
  -ContentType "application/json" `
  -Body $body `
  -TimeoutSec 600

$decodeTps = if ($response.eval_duration -gt 0) {
  [math]::Round(($response.eval_count * 1000000000.0) / $response.eval_duration, 2)
} else {
  0
}

$promptTps = if ($response.prompt_eval_duration -gt 0) {
  [math]::Round(($response.prompt_eval_count * 1000000000.0) / $response.prompt_eval_duration, 2)
} else {
  0
}

[pscustomobject]@{
  model = $Model
  decode_tokens = $response.eval_count
  decode_tps = $decodeTps
  prompt_tokens = $response.prompt_eval_count
  prompt_tps = $promptTps
  load_ms = [math]::Round($response.load_duration / 1000000.0, 1)
}

$ollama = Get-Command ollama -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
if (-not $ollama) {
  $candidate = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
  if (Test-Path $candidate) {
    $ollama = $candidate
  }
}
if ($ollama) {
  & $ollama ps
}
