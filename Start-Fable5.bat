@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)

".venv\Scripts\python.exe" -m pip install -r requirements.txt

if not exist "models\gemma-4-12B-it-Q4_K_M.gguf" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\download-gemma-q4.ps1"
  echo.
  echo Gemma Q4 download is not complete yet.
  echo Leave the BITS download running, then run Start-Fable5.bat again.
  echo You can check progress with: powershell Get-BitsTransfer
  pause
  exit /b 0
)

start "Fable5 Model Server" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-llama-server.ps1"
timeout /t 8 /nobreak >nul
start "Fable5 UI Backend" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-backend.ps1"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:7860/"

echo Fable5 is starting at http://127.0.0.1:7860/
echo The first Q4 model start can take a while because llama.cpp downloads the model.
pause
