# Install (or reinstall) the scheduled task that renders the day's videos automatically.
# Usage: powershell -ExecutionPolicy Bypass -File demo\windows\install.ps1
# Uninstall: powershell -ExecutionPolicy Bypass -File demo\windows\install.ps1 -Uninstall
param([switch]$Uninstall)
$ErrorActionPreference = 'Stop'
$Name = 'TinTuc AutoRender'
$Project = (Resolve-Path "$PSScriptRoot\..\..").Path
Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
if ($Uninstall) { Write-Output "Da go $Name"; exit 0 }

$Python = Join-Path $Project '.venv-demo\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Khong thay $Python; cai dat theo demo/README.md truoc" }
New-Item -ItemType Directory -Force (Join-Path $Project 'output') | Out-Null
# A headless console: auto_render.py and the node, ffmpeg and TTS processes it starts open no window.
$Command = "set PYTHONUTF8=1&& `"$Python`" demo\auto_render.py >> output\auto-render.log 2>&1"
$Action = New-ScheduledTaskAction -Execute 'conhost.exe' -Argument "--headless cmd.exe /c `"$Command`"" -WorkingDirectory $Project
# Every 10 minutes all day; auto_render.py itself skips runs outside auto_render.window.
$Daily = New-ScheduledTaskTrigger -Daily -At '00:00'
$Daily.Repetition = (New-ScheduledTaskTrigger -Once -At '00:00' -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$Logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Daily, $Logon -Settings $Settings `
    -Description 'Kiem tra Google Sheet moi 10 phut va tao video diem tin (demo/auto_render.py)' | Out-Null
Write-Output "Da cai '$Name': kiem tra Sheet moi 10 phut trong khung gio auto_render.window cua demo/config.json. Nhat ky: output\auto-render.log"
