@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo An error occurred. Press any key to close.
    pause >nul
)
