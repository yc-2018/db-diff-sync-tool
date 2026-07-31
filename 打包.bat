@echo off
rem Keep this file ASCII-only; cmd parses it as GBK on Chinese Windows.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Please run the init bat once first.
  pause
  exit /b 1
)

set "PY=.venv\Scripts\python.exe"
set "PYINSTALLER=.venv\Scripts\pyinstaller.exe"
set "APP_NAME=数据库同步比对工具"
set "ORACLE_CLIENT=.oracle_client\instantclient_21_22"

"%PY%" -m pip install --upgrade pyinstaller
if errorlevel 1 (
  echo [ERROR] Failed to install pyinstaller.
  pause
  exit /b 1
)

set "EXTRA_DATA="
if exist "%ORACLE_CLIENT%\oci.dll" (
  echo Oracle Instant Client found: %ORACLE_CLIENT%
  set "EXTRA_DATA=--add-data %ORACLE_CLIENT%;.oracle_client\instantclient_21_22"
) else (
  echo [WARN] Oracle Instant Client not found at %ORACLE_CLIENT%.
  echo [WARN] Oracle 11g requires thick mode. Put Instant Client files there before packaging.
)

"%PYINSTALLER%" --noconfirm --windowed --name "%APP_NAME%" ^
  --collect-all oracledb ^
  --add-data "web;web" ^
  %EXTRA_DATA% ^
  app.py
if errorlevel 1 (
  echo [ERROR] Packaging failed.
  pause
  exit /b 1
)

echo.
echo Package done: dist\%APP_NAME%\
pause
