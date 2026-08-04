@echo off
rem Keep this file ASCII-only; cmd parses it as GBK on Chinese Windows.
setlocal

set "DATA_DIR=%USERPROFILE%\.dbsync_tool"

echo This will delete all local user data for DBSyncTool:
echo.
echo   %DATA_DIR%
echo.
echo It includes saved connections, session state, compare history, and WebView cache.
echo This operation cannot be undone.
echo.
set /p CONFIRM=Type DELETE to continue: 
if not "%CONFIRM%"=="DELETE" (
  echo Cancelled.
  pause
  exit /b 0
)

if "%USERPROFILE%"=="" (
  echo [ERROR] USERPROFILE is empty. Abort.
  pause
  exit /b 1
)

if not exist "%DATA_DIR%" (
  echo No local user data found.
  pause
  exit /b 0
)

rd /s /q "%DATA_DIR%"
if errorlevel 1 (
  echo [ERROR] Failed to delete local user data. Close the app and try again.
  pause
  exit /b 1
)

echo Local user data deleted.
pause
