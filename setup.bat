@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [GENSO] Python 3.12 virtual environment is being created...
  py -V:3.12 -m venv .venv
  if errorlevel 1 goto :error
)

echo [GENSO] Installing pywebview...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install pywebview
if errorlevel 1 goto :error

echo.
echo [GENSO] Setup completed. Next, run run.bat to start the app.
pause
exit /b 0

:error
echo.
echo [GENSO] Setup failed. Review the messages above.
pause
exit /b 1
