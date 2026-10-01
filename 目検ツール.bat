@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0app.py"
) else (
    python "%~dp0app.py"
)
pause
