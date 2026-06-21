@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ── Step 0: Git pull ──
echo [0/7] Pulling latest code...
git pull --ff-only 2>&1 || echo   WARNING: git pull failed

:: ── Step 0.5: Clear old cache ──
echo [0.5/7] Clearing old cache...
if exist "debug" rd /s /q "debug" 2>nul
if exist "hunter.log" del /q "hunter.log" 2>nul
echo   Done.

:: ── Step 1: Kill port 7861 ──
echo [1/7] Checking port 7861...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7861" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
timeout /t 2 /nobreak >nul 2>&1
echo   Done.

:: ── Step 1.5: Clear stop flag ──
echo [1.5/7] Clearing stop flag...
if exist "stop.flag" del /q stop.flag 2>nul
echo   Done.
timeout /t 1 /nobreak >nul 2>&1

:: ── Step 2: Clear Python cache ──
echo [2/7] Clearing Python cache...
for /r . %%f in (*.pyc) do @del /q "%%f" 2>nul
for /d /r . %%d in (__pycache__) do @echo "%%d" | findstr /c:".venv" >nul || (if exist "%%d" rd /s /q "%%d" 2>nul)
echo   Done.
timeout /t 1 /nobreak >nul 2>&1

:: ── Step 3: Virtual env ──
echo [3/7] Checking virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo   Creating venv...
    python -m venv .venv
)
set "PYTHON=.venv\Scripts\python.exe"

:: ── Step 4: Install deps ──
echo [4/7] Installing dependencies...
"%PYTHON%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)
"%PYTHON%" -m playwright install chromium 2>nul

:: ── Step 5: Test imports ──
echo [5/7] Testing imports...
"%PYTHON%" -c "import fastapi, uvicorn, playwright; print('All imports OK')" 2>&1
if errorlevel 1 (
    echo ERROR: Import test failed
    pause
    exit /b 1
)

:: ── Step 6: Start server ──
echo [6/7] Starting FastAPI Web UI...
start "InternshipHunter" cmd /K "%PYTHON% web_ui.py"

:: Wait for server (poll port 7861, max 60s)
set /a attempts=0
:waitloop
set /a attempts+=1
if %attempts% gtr 60 (
    echo   ERROR: Server did not start within 60 seconds
    pause
    exit /b 1
)
powershell -Command "try { $tcp = New-Object System.Net.Sockets.TcpClient; $result = $tcp.BeginConnect('127.0.0.1', 7861, $null, $null); $wait = $result.AsyncWaitHandle.WaitOne(500, $false); if ($wait) { $tcp.EndConnect($result); $tcp.Close(); exit 0 } else { $tcp.Close(); exit 1 } } catch { exit 1 }" 2>nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul 2>&1
    goto waitloop
)

:: ── Step 7: Open browser ──
echo [7/7] Opening browser...
timeout /t 2 /nobreak >nul 2>&1
start "" http://127.0.0.1:7861

:: ── Step 8: Tail hunter.log ──
echo ========================================
echo   Internship Hunter is RUNNING
echo   UI:  http://127.0.0.1:7861
echo   Server logs: "InternshipHunter" window
echo   Live log below (Ctrl+C to stop tail):
echo ========================================

:tailloop
:: Wait for hunter.log to exist, then tail it
:waitlog
if not exist "hunter.log" (
    timeout /t 1 /nobreak >nul 2>&1
    goto waitlog
)
powershell -NoProfile -Command "try { Get-Content -Path 'hunter.log' -Tail 20 -Wait -Encoding UTF8 } catch {}"
:: If we get here, either Ctrl+C or server died
:: Check if server is still up
netstat -ano | findstr ":7861" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    :: Server still up, re-tail (user pressed Ctrl+C in tail)
    goto tailloop
)
echo.
echo [Server stopped]
pause
