@echo off
setlocal

set "PROFILE=%~1"
set "TARGET=%~2"

if "%PROFILE%"=="" goto :usage
if "%TARGET%"=="" goto :usage

echo [*] Initializing order workspace: "%TARGET%"
echo [*] Profile: %PROFILE%

if not exist "%TARGET%\.roo" mkdir "%TARGET%\.roo"

copy /y "%~dp0mcp_configs\mcp.%PROFILE%.json" "%TARGET%\.roo\mcp.json" >nul
xcopy /e /i /y "%~dp0.roo" "%TARGET%\.roo" >nul
copy /y "%~dp0.roomodes" "%TARGET%\.roomodes" >nul

echo.
echo ========================================================
echo Order workspace initialized successfully: "%TARGET%"
echo Selected profile: %PROFILE%
echo Open this folder in VS Code and select mode in Roo Code.
echo ========================================================
exit /b 0

:usage
echo Usage: new_order.bat intake ^| vpf ^| report ^| ext "TARGET_FOLDER"
echo Example: new_order.bat vpf "C:\Orders\Заказ 1 Печать чека"
exit /b 1
