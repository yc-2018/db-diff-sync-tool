@echo off
rem Keep this file ASCII-only; cmd parses it as GBK on Chinese Windows.
cd /d "%~dp0"
echo Initializing environment (first time only)...

rem Prefer Python 3.12 (LTS, all wheels available). Fall back to PATH python.
set "PY312=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PY312%" (
  set "PY=%PY312%"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.12+ first.
    pause
    exit /b 1
  )
  set "PY=python"
)

echo Using: %PY%
"%PY%" -m venv .venv
if errorlevel 1 (
  echo [ERROR] Failed to create virtual environment.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements.txt
echo.
echo Init done. From now on just double-click the start bat.
pause
