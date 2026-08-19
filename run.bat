@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [GENSO] .venv was not found. Run setup.bat first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "wrapper\app.py"
if errorlevel 1 (
  echo.
  echo [GENSO] The application ended with an error. See engine.log if the engine failed to start.
  pause
)
