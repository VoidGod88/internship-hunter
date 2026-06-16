@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "PYTHON=%VENV_DIR%\Scripts\python.exe"

echo ========================================
echo   WIE Internship Hunter v4 - Launcher
echo ========================================
echo.

:: Step 1: Create venv if missing
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    C:\Users\wumian\.workbuddy\binaries\python\versions\3.13.12\python.exe -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo [FAIL] Could not create venv
        pause
        exit /b 1
    )
) else (
    echo [1/3] Virtual environment found.
)

:: Step 2: Install/update dependencies
echo [2/3] Installing dependencies...
"%PYTHON%" -m pip install -q --upgrade pip
if !errorlevel! neq 0 (
    echo [FAIL] pip upgrade failed
    pause
    exit /b 1
)

"%PYTHON%" -m pip install -q -r requirements.txt
if !errorlevel! neq 0 (
    echo [FAIL] Dependency installation failed
    pause
    exit /b 1
)

:: Ensure Playwright browsers installed
"%PYTHON%" -m playwright install chromium 2>nul

:: Step 3: Launch
echo [3/3] Starting Gradio UI...
echo.
echo   Open http://127.0.0.1:7861 in browser
echo   Press Ctrl+C to stop
echo.
"%PYTHON%" app.py

pause
