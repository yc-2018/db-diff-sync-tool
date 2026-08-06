@echo off
rem Keep this file ASCII-only; cmd parses it as GBK on Chinese Windows.
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] .venv not found. Please run the init bat once first.
  pause
  exit /b 1
)
start "DBSync MCP Configurator" /b ".venv\Scripts\pythonw.exe" "mcp\configurator.py"
exit /b 0
