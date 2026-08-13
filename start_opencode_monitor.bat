@echo off
setlocal

set "ROOT=%~dp0"
set "PORT=18088"
if defined OPENCODE_MONITOR_PORT set "PORT=%OPENCODE_MONITOR_PORT%"

start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ^
    "$root = '%ROOT%'; $url = 'http://127.0.0.1:%PORT%'; $running = $false; try { $running = (Invoke-WebRequest -UseBasicParsing -Uri ($url + '/api/server/status') -TimeoutSec 1).StatusCode -eq 200 } catch {}; if (-not $running) { $pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source; $run = Join-Path $root 'run.py'; Start-Process -FilePath $pythonw -ArgumentList @('-X','utf8',$run) -WorkingDirectory $root -WindowStyle Hidden }; $deadline = (Get-Date).AddSeconds(20); while ((Get-Date) -lt $deadline) { try { if ((Invoke-WebRequest -UseBasicParsing -Uri ($url + '/api/server/status') -TimeoutSec 1).StatusCode -eq 200) { Start-Process $url; break } } catch {}; Start-Sleep -Milliseconds 300 }"

endlocal
