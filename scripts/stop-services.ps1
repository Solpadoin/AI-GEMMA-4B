$ErrorActionPreference = "SilentlyContinue"

Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -eq "llama-server.exe" -or
    ($_.CommandLine -like "*uvicorn app:app*" -and $_.Name -like "python*")
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Write-Host "Fable5 local services stopped."
