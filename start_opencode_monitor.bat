@echo off
setlocal
cd /d "%~dp0"

set PORT=18088
if defined OPENCODE_MONITOR_PORT set PORT=%OPENCODE_MONITOR_PORT%
set URL=http://127.0.0.1:%PORT%

:: 1. Find Python
set PYTHON_EXE=
for /f "delims=" %%P in ('where.exe python.exe 2^>nul') do if not defined PYTHON_EXE set PYTHON_EXE=%%P
for /f "delims=" %%P in ('where.exe pythonw.exe 2^>nul') do if not defined PYTHON_EXE set PYTHON_EXE=%%P
if not defined PYTHON_EXE if exist C:\Python314\python.exe set PYTHON_EXE=C:\Python314\python.exe
if not defined PYTHON_EXE if exist C:\Python313\python.exe set PYTHON_EXE=C:\Python313\python.exe

if not defined PYTHON_EXE (
    echo [Error] Python not found!
    pause
    exit /b 1
)

:: 2. Check if already running
"%PYTHON_EXE%" -c "import urllib.request,sys; urllib.request.urlopen('%URL%/api/server/status',timeout=1); sys.exit(0)" >nul 2>&1 && goto :OPEN_BROWSER

:: 3. Launch backend
echo [Info] Starting OpenCode Monitor...
set OPENCODE_MONITOR_RESTART_DELAY=0
"%PYTHON_EXE%" -X utf8 backend_launcher.py

:: 4. Wait for server
set /a TRIES=0
:WAIT
"%PYTHON_EXE%" -c "import time; time.sleep(1)"
"%PYTHON_EXE%" -c "import urllib.request,sys; urllib.request.urlopen('%URL%/api/server/status',timeout=1); sys.exit(0)" >nul 2>&1 && goto :OPEN_BROWSER
set /a TRIES+=1
if %TRIES% lss 15 goto :WAIT

echo [Warning] Timeout. Opening browser anyway...
start "" %URL%
goto :END

:OPEN_BROWSER
start "" %URL%

:END
endlocal
