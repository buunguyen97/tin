# Install (or reinstall) the Telegram reup bot as a task that starts at logon and keeps running.
# Usage: powershell -ExecutionPolicy Bypass -File demo\windows\install-bot.ps1
# Uninstall: powershell -ExecutionPolicy Bypass -File demo\windows\install-bot.ps1 -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$Name = 'TinTuc Telegram Bot'
$Project = (Resolve-Path "$PSScriptRoot\..\..").Path
Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
# Stopping the task leaves the bot itself running, and its lock would then make the new one exit.
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'demo\\telegram_bot\.py' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($Uninstall) { Write-Output "Da go $Name"; exit 0 }

$Python = Join-Path $Project '.venv-demo\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Khong thay $Python; cai dat theo demo/README.md truoc" }
New-Item -ItemType Directory -Force (Join-Path $Project 'output') | Out-Null
# A headless console, as for the auto render: the bot and the processes it starts open no window.
$Command = "set PYTHONUTF8=1&& `"$Python`" demo\telegram_bot.py >> output\telegram-bot.log 2>&1"
$Action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless cmd.exe /c `"$Command`"" -WorkingDirectory $Project
$Logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# No time limit; if the bot stops (lost network at start, a crash) Task Scheduler starts it again.
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Logon -Settings $Settings `
    -Description 'Bot Telegram dang lai video kem giong doc (demo/telegram_bot.py)' | Out-Null
Start-ScheduledTask -TaskName $Name
Write-Output "Da cai va chay '$Name'. Nhat ky: output\telegram-bot.log"
