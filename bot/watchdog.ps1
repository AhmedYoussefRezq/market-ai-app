# Keeps the MT5 demo terminal and the M15 pullback bot alive.
# Started automatically at logon by the "MarketAI-Demo-Bot" scheduled task.
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
$bot = Join-Path $root "bot.py"
$runLog = Join-Path $root "run.log"
$wdLog = Join-Path $root "watchdog.log"
$mt5 = "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"

function WLog($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $wdLog -Value $line -Encoding UTF8
}

WLog "watchdog started (pid $PID)"
$fastFails = 0

while ($true) {
    try {
        $term = Get-Process -Name terminal64 -ErrorAction SilentlyContinue
        if (-not $term) {
            WLog "MT5 terminal not running - starting it"
            if (Test-Path $mt5) { Start-Process -FilePath $mt5 | Out-Null }
            Start-Sleep -Seconds 30
        }

        $botProc = Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*bot.py*--live-demo*" }

        if (-not $botProc) {
            WLog "bot not running - starting"
            $started = Get-Date
            $p = Start-Process -FilePath "cmd.exe" `
                -ArgumentList "/c", "`"$root\run-bot.cmd`"" `
                -WorkingDirectory $root -WindowStyle Hidden -PassThru
            $p.WaitForExit()
            $secs = [int]((Get-Date) - $started).TotalSeconds
            if ($secs -lt 60) { $fastFails++ } else { $fastFails = 0 }
            WLog "bot exited after ${secs}s (code $($p.ExitCode), fastFails=$fastFails)"
            if ($fastFails -ge 3) {
                WLog "repeated fast failures - waiting 5 min. Check MT5 login and the Algo Trading button."
                Start-Sleep -Seconds 300
                $fastFails = 0
            } else {
                Start-Sleep -Seconds 20
            }
        } else {
            Start-Sleep -Seconds 30
        }
    } catch {
        WLog "watchdog error: $_"
        Start-Sleep -Seconds 30
    }
}
