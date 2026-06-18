@echo off
setlocal EnableDelayedExpansion

:: ── Step 0: Kill existing processes on port 7861 ──
echo [1/5] Checking port 7861...
powershell -Command "try { $p = Get-NetTCPConnection -LocalPort 7861 -State Listen -ErrorAction SilentlyContinue; if ($p) { Stop-Process -Id $p.OwningProcess -Force -ErrorAction SilentlyContinue; Write-Host '  Killed existing process on port 7861' } } catch {}"
:: Also kill orphaned python processes running web_ui.py or app.py
powershell -Command "Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*web_ui.py*' -or $_.CommandLine -like '*app.py*' } | ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; Write-Host ('  Killed orphaned python (PID=' + $_.Id + ')') }"
timeout /t 2 /nobreak >nul 2>&1

:: ── Step 1: Virtual env ──
echo [2/5] Checking virtual environment...
if not exist ".venv\Scripts\python.exe" (
    echo   Creating venv...
    python -m venv .venv
)
set "PYTHON=.venv\Scripts\python.exe"

:: ── Step 2: Install deps ──
echo [3/5] Installing dependencies...
"%PYTHON%" -m pip install -q -r requirements.txt 2>nul
"%PYTHON%" -m playwright install chromium 2>nul

:: ── Step 3: Start server in background window ──
echo [4/5] Starting FastAPI Web UI...
start "InternshipHunter" "%PYTHON%" web_ui.py
:: Wait for server to be ready (poll port 7861)
set /a attempts=0
:waitloop
set /a attempts+=1
if %attempts% gtr 60 (
    echo   ERROR: Server did not start within 30 seconds
    echo   Check hunter.log for errors
    pause
    exit /b 1
)
:: Use simpler port check (compatible with more PowerShell versions)
powershell -Command "try { $tcp = New-Object System.Net.Sockets.TcpClient; $result = $tcp.BeginConnect('127.0.0.1', 7861, $null, $null); $wait = $result.AsyncWaitHandle.WaitOne(500, $false); if ($wait) { $tcp.EndConnect($result); $tcp.Close(); exit 0 } else { $tcp.Close(); exit 1 } } catch { exit 1 }" 2>nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul 2>&1
    goto waitloop
)

:: ── Step 4: Auto-open browser ──
echo [5/5] Opening browser...
start "" http://127.0.0.1:7861

:: ── Step 5: Tail live log in this console ──
echo.
echo ========================================
echo   Internship Hunter is RUNNING
echo   UI:  http://127.0.0.1:7861
echo   Live log below (Ctrl+C to stop):
echo ========================================
echo.
powershell -NoProfile -Command "Get-Content -Path 'hunter.log' -Wait -Tail 30 -Encoding UTF8"
