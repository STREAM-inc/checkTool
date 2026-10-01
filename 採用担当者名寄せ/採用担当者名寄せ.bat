@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0fill_recruiter.py"
) else (
    python "%~dp0fill_recruiter.py"
)

echo.
pause
