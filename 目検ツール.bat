@echo off
chcp 932 >nul
setlocal
rem 目検ツール の app.py を起動する（checkTool フォルダに置いたまま使う）
cd /d "%~dp0目検ツール"

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0目検ツール\app.py"
) else (
    python "%~dp0目検ツール\app.py"
)

echo.
pause
