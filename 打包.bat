@echo off
rem Keep this file ASCII-only; cmd parses it as GBK on Chinese Windows.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Please run the init bat once first.
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"

"%PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [ERROR] Failed to install pyinstaller.
  pause
  exit /b 1
)

"%PY%" package_windows.py
if errorlevel 1 (
  echo [ERROR] Packaging failed.
  pause
  exit /b 1
)

echo.
echo Package done. See the dist folder.
pause
