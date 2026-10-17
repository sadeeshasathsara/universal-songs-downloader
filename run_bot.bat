@echo off
setlocal
cd /d "%~dp0"
title Universal Songs Downloader - Telegram Bot

echo ========================================================
echo   Starting Universal Songs Downloader Telegram Bot...
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run_bot.py
) else (
    python run_bot.py
)

if %ERRORLEVEL% neq 0 (
    echo.
    echo Bot stopped with an error. Press any key to exit.
    pause >nul
)
