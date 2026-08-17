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

where python.exe >nul 2>&1
if not errorlevel 1 set "PYTHON_EXE=python.exe"

if not defined PYTHON_EXE (
    where pythonw.exe >nul 2>&1
    if not errorlevel 1 set "PYTHON_EXE=pythonw.exe"
)

if not defined PYTHON_EXE (
    if exist "C:\Python314\python.exe" set "PYTHON_EXE=C:\Python314\python.exe"
    if exist "C:\Python313\python.exe" set "PYTHON_EXE=C:\Python313\python.exe"
    if exist "C:\Python312\python.exe" set "PYTHON_EXE=C:\Python312\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python314\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
    if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
)

if not defined PYTHON_EXE (
    echo.
    echo [Error] Python (python.exe / pythonw.exe) not found!
    echo Please install Python or add it to PATH.
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlopen('%URL%/api/server/status', timeout=1)" >nul 2>&1
if !errorlevel! equ 0 (
    echo [Notice] Monitor service is already running. Opening browser...
    start "" "%URL%"
    ping 127.0.0.1 -n 3 >nul
    goto :END
)

echo [Info] Starting OpenCode Monitor in background...
set "OPENCODE_MONITOR_RESTART_DELAY=0"
"%PYTHON_EXE%" -X utf8 backend_launcher.py

echo [Info] Waiting for web server response...
set "SUCCESS=0"
for /l %%i in (1,1,15) do (
    if !SUCCESS! equ 0 (
        "%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlopen('%URL%/api/server/status', timeout=1)" >nul 2>&1
        if !errorlevel! equ 0 (
            set "SUCCESS=1"
        ) else (
            ping 127.0.0.1 -n 2 >nul
        )
    )
)

if !SUCCESS! equ 1 (
    echo [Success] OpenCode Monitor is ready! Opening browser...
    start "" "%URL%"
    ping 127.0.0.1 -n 3 >nul
) else (
    echo.
    echo [Warning] Server response timeout. Opening browser anyway...
    echo If page does not load, please check port %PORT%.
    echo.
    start "" "%URL%"
    pause
)

:END
endlocal
