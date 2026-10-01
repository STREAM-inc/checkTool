@echo off
rem Sets up the company proxy for Backlog / GitHub / pip (no admin needed)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_proxy.ps1" %*
echo.
pause
