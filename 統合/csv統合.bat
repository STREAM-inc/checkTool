@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0merge_csv.py"
) else (
    python "%~dp0merge_csv.py"
)

echo.
pause
