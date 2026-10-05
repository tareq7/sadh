@echo off
title Sadh (Groq Voice Typing | صَدْح)
cd /d "%~dp0"
if exist "Sadh.exe" (
    Sadh.exe
) else (
    python main.py
)
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Application exited with error code %ERRORLEVEL%
    pause
)
