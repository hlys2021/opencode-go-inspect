$root = $PSScriptRoot
$port = if ($env:OPENCODE_MONITOR_PORT) { $env:OPENCODE_MONITOR_PORT } else { "18088" }
$url = "http://127.0.0.1:$port"

# 检查服务是否已在线
$running = $false
try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/server/status" -TimeoutSec 1
    if ($resp.StatusCode -eq 200) {
        $running = $true
    }
} catch {}

if (-not $running) {
    function Get-PythonPath {
        $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }

        $candidates = @(
            "$env:LocalAppData\Programs\Python\Python*\pythonw.exe",
            "$env:LocalAppData\Programs\Python\Python*\python.exe",
            "C:\Python*\pythonw.exe",
            "C:\Python*\python.exe"
        )
        foreach ($pat in $candidates) {
            $found = Get-Item $pat -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { return $found.FullName }
        }
        return $null
    }

    $pythonPath = Get-PythonPath
    if (-not $pythonPath) {
        Write-Error "未找到 Python 解释器 (pythonw.exe / python.exe)"
        exit 1
    }

    $runPy = Join-Path $root "run.py"
    Start-Process -FilePath $pythonPath -ArgumentList @("-X", "utf8", "`"$runPy`"") -WorkingDirectory $root -WindowStyle Hidden
}

# 等待服务就绪并打开浏览器
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/server/status" -TimeoutSec 1
        if ($resp.StatusCode -eq 200) {
            Start-Process $url
            break
        }
    } catch {}
    Start-Sleep -Milliseconds 300
}
