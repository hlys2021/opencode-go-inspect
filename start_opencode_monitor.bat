@echo off
setlocal
set "ROOT=%~dp0"
start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT%start_opencode_monitor.ps1"
endlocal
