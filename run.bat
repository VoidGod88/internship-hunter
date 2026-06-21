@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul 2>&1

:: ── Step 0: Git pull (get latest code) ──
echo [0/7] Pulling latest code...
git pull --ff-only 2>&1 || echo   WARNING: git pull failed (resolve conflicts manually or commit local changes)
echo.

:: ── Step 0.5: Clear old cache files ──
echo [0.5/7] Clearing old cache...
if exist "debug" rd /s /q "debug" 2>nul
if exist "hunter.log" del /q "hunter.log" 2>nul
echo   Done.
echo.

:: ── Step 1: Kill existing processes on port 7861 ──
echo [1/7] Checking port 7861...
powershell -Command "try { $p = Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '  Killed existing process on port 7861' } } catch {}"

:: Kill orphaned python processes running web_ui.py / hunter.py
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*web_ui.py*' -or $_.CommandLine -like '*hunter.py*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; Write-Host ('  Killed orphaned python (PID=' + $_.Id + ')') } 2>nul"

:: Kill Playwright browser processes (Chromium msedge.exe)
powershell -Command "Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*playwright*' -or $_.CommandLine -like '*chromium*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; Write-Host ('  Killed playwright browser (PID=' + $_.Id + ')') } 2>nul"

timeout /t 2 /nobreak >nul 2>&1

:: ── Step 1.5: Clear stop.flag (prevent old stop from affecting new run) ──
echo [1.5/7] Clearing stop flag...
if exist "stop.flag" (
    del /q stop.flag 2>nul
    echo   Cleared stop.flag
)
echo   Done.
timeout /t 1 /nobreak >nul 2>&1

:: ── Step 2: Clear __pycache__ (prevent stale .pyc loading) ──
echo [2/7] Clearing Python cache...
:: Delete .pyc files
for /r . %%f in (*.pyc) do @del /q "%%f" 2>nul
:: Delete __pycache__ dirs (skip .venv to avoid locked-dir errors)
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
"%PYTHON%" -m playwright install chromium
if errorlevel 1 (
    echo WARNING: Failed to install Chromium (optional)
)

:: ── Step 5: Test imports ──
echo [5/7] Testing imports...
"%PYTHON%" -c "import fastapi, uvicorn, playwright; print('All imports OK')" 2>&1
if errorlevel 1 (
    echo ERROR: Failed to import required modules. Check error above.
    pause
    exit /b 1
)

:: ── Step 6: Start server (keep window open if crashed) ──
echo [6/7] Starting FastAPI Web UI...
:: Use cmd /K to keep window open (so you can see errors)
start "InternshipHunter" cmd /K "%PYTHON% web_ui.py"

:: Wait for server to be ready (poll port 7861)
set /a attempts=0
:waitloop
set /a attempts+=1
if %attempts% gtr 60 (
    echo   ERROR: Server did not start within 30 seconds
    echo   Check the "InternshipHunter" window for errors
    pause
    exit /b 1
)
powershell -Command "try { $tcp = New-Object System.Net.Sockets.TcpClient; $result = $tcp.BeginConnect('127.0.0.1', 7861, $null, $null); $wait = $result.AsyncWaitHandle.WaitOne(500, $false); if ($wait) { $tcp.EndConnect($result); $tcp.Close(); exit 0 } else { $tcp.Close(); exit 1 } } catch { exit 1 }" 2>nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul 2>&1
    goto waitloop
)

:: ── Step 7: Auto-open browser ──
echo [7/7] Opening browser...
timeout /t 2 /nobreak >nul 2>&1
start "" http://127.0.0.1:7861

:: ── Step 8: Tail live log (auto-exit when server stops or stop.flag appears) ──
echo.
echo ========================================
echo   Internship Hunter is RUNNING
echo   UI:  http://127.0.0.1:7861
echo   Server window: "InternshipHunter" (check for errors)
echo   Live log below (Ctrl+C to stop):
echo ========================================
echo.

:: Use PowerShell to tail log, but exit when stop.flag appears or server process exits
powershell -NoProfile -Command "& { $lastSize = 0; while ($true) { if (Test-Path 'stop.flag') { Write-Host '[Stop flag detected, exiting...]'; break }; $proc = Get-Process -Id (Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue).OwningProcess -ErrorAction SilentlyContinue; if (-not $proc) { Write-Host '[Server stopped, exiting...]'; break }; if (Test-Path 'hunter.log') { $content = Get-Content 'hunter.log' -Tail 30 -Encoding UTF8; $newSize = (Get-Item 'hunter.log').Length; if ($newSize -ne $lastSize) { Clear-Host; Write-Host $content -NoNewline; $lastSize = $newSize } }; Start-Sleep -Milliseconds 500 } }"

echo.
echo ===== Internship Hunter stopped =====
pause
