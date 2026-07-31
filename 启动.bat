@echo off
rem UTF-8 Chinese chars in .bat break cmd parsing on GBK systems, keep ASCII only.
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] .venv not found. Please run the init bat once first.
  pause
  exit /b 1
)
start "DBSyncTool" ".venv\Scripts\pythonw.exe" "app.py"
