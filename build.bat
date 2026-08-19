@echo off
setlocal
cd /d "%~dp0"

echo [BUILD] Building LinkHUB-MiniMax-Editor.exe ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name "LinkHUB-MiniMax-Editor" ^
  --distpath "build_exe\dist" --workpath "build_exe\work" --specpath "build_exe" ^
  "wrapper\app.py"
if errorlevel 1 goto :error

echo [BUILD] Staging runtime files ...
xcopy /E /I /Y /Q "genso" "build_exe\dist\LinkHUB-MiniMax-Editor\genso" >nul
if exist "build_exe\dist\LinkHUB-MiniMax-Editor\genso\__pycache__" rd /S /Q "build_exe\dist\LinkHUB-MiniMax-Editor\genso\__pycache__"
copy /Y "README.md" "build_exe\dist\LinkHUB-MiniMax-Editor\" >nul
copy /Y "LICENSE" "build_exe\dist\LinkHUB-MiniMax-Editor\" >nul

echo.
echo [BUILD] Done: build_exe\dist\LinkHUB-MiniMax-Editor\
pause
exit /b 0

:error
echo.
echo [BUILD] Build failed.
pause
exit /b 1
