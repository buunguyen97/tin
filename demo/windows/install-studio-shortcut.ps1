# Put a "Dien tin Studio" shortcut on the desktop: it starts Studio in a minimised window and opens the TikTok tab.
# Closing that window stops Studio. Automatic posting runs in the bot and does not need it.
# Usage: powershell -ExecutionPolicy Bypass -File demo\windows\install-studio-shortcut.ps1
$ErrorActionPreference = 'Stop'
$Project = (Resolve-Path "$PSScriptRoot\..\..").Path
$Python = Join-Path $Project '.venv-demo\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "Khong thay $Python; cai dat theo demo/README.md truoc" }
$Path = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Dien tin Studio.lnk'
$Shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
$Shortcut.TargetPath = $Python
$Shortcut.Arguments = '-X utf8 demo\studio.py --open'
$Shortcut.WorkingDirectory = $Project
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Mo Diem tin Studio'
$Shortcut.Save()
Write-Output "Da tao $Path"
