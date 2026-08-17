@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PORT=18088"
if defined OPENCODE_MONITOR_PORT set "PORT=%OPENCODE_MONITOR_PORT%"
set "URL=http://127.0.0.1:%PORT%"

echo ==================================================
echo   OpenCode Usage Monitor Starting...
echo ==================================================

set "PYTHON_EXE="

where pythonw.exe >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=pythonw.exe"

if not defined PYTHON_EXE (
    where python.exe >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=python.exe"
)

if not defined PYTHON_EXE (
    if exist "C:\Python314\pythonw.exe" set "PYTHON_EXE=C:\Python314\pythonw.exe"
    if exist "C:\Python313\pythonw.exe" set "PYTHON_EXE=C:\Python313\pythonw.exe"
    if exist "C:\Python312\pythonw.exe" set "PYTHON_EXE=C:\Python312\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python314\pythonw.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
)

if not defined PYTHON_EXE (
    echo.
    echo [Error] Python (pythonw.exe / python.exe) not found!
    echo Please install Python or add it to PATH.
    echo.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$r = $false; try { $r = (Invoke-WebRequest -UseBasicParsing -Uri '%URL%/api/server/status' -TimeoutSec 1 -ErrorAction Stop).StatusCode -eq 200 } catch {}; if ($r) { exit 0 } else { exit 1 }" >nul 2>&1
if !errorlevel! equ 0 (
    echo [Notice] Monitor service is already running. Opening browser...
    start %URL%
    timeout /t 2 >nul
    goto :END
)

echo [Info] Starting OpenCode Monitor in background...
start "" "%PYTHON_EXE%" -X utf8 "%~dp0run.py"

echo [Info] Waiting for web server response...
set "SUCCESS=0"
for /l %%i in (1,1,20) do (
    if !SUCCESS! equ 0 (
        powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$r = $false; try { $r = (Invoke-WebRequest -UseBasicParsing -Uri '%URL%/api/server/status' -TimeoutSec 1 -ErrorAction Stop).StatusCode -eq 200 } catch {}; if ($r) { exit 0 } else { exit 1 }" >nul 2>&1
        if !errorlevel! equ 0 (
            set "SUCCESS=1"
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)

if !SUCCESS! equ 1 (
    echo [Success] OpenCode Monitor is ready! Opening browser...
    start %URL%
    ping 127.0.0.1 -n 3 >nul
) else (
    echo [Warning] Server response timeout. Opening browser anyway...
    start %URL%
    pause
)

:END
endlocal
