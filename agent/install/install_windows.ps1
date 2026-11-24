<#
Install (or update) the lab monitor agent on Windows: hidden scheduled tasks that start at boot as SYSTEM.

Before running (once per computer):
  1. Install Python from python.org with "Add python.exe to PATH" and "Install for all users" ticked
     (the Microsoft Store Python is per-user and SYSTEM cannot see it).
  2. In an admin terminal:  py -m pip install psutil
  3. Download LibreHardwareMonitor, unzip it to C:\Program Files\LibreHardwareMonitor, open it once
     as administrator and enable Options > Remote Web Server > Run (port 8085). Then close it.

First install, from the agent\ folder, in an ADMIN PowerShell:
  powershell -ExecutionPolicy Bypass -File install\install_windows.ps1

It asks for your GitHub username and token (first time only), then the computer's name and lab.
Later, to change name or lab: double-click "Change name or lab" in C:\Program Files\LabMonitor
(or run this script again). To enter a new token, run it with -NewToken.
#>
param(
    [switch]$NewToken,      # ask for a new token (when the old one expires)
    [string]$LhmExe = "C:\Program Files\LibreHardwareMonitor\LibreHardwareMonitor.exe",
    [string]$InstallDir = "C:\Program Files\LabMonitor"
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in an administrator PowerShell."
}

$src = Split-Path -Parent $PSScriptRoot
$setup = Join-Path $InstallDir "setup"      # installed copy of this folder, used by "Change name or lab"

# ------------------------------------------------------------------ questions
$cfgPath = "$src\config.json"
# the installed settings are the reference: start from them if this computer is already installed
if ((Test-Path "$InstallDir\config.json") -and ((Resolve-Path $src).Path -ne $InstallDir)) {
    Copy-Item "$InstallDir\config.json" $cfgPath -Force
}
$cfg = [ordered]@{}
if (Test-Path $cfgPath) {
    $j = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($p in $j.PSObject.Properties) { $cfg[$p.Name] = $p.Value }
}
function Ask($prompt, $default) {
    $label = if ($default) { "$prompt [$default]" } else { $prompt }
    $r = Read-Host $label
    if ([string]::IsNullOrWhiteSpace($r)) { return $default } else { return $r.Trim() }
}
Write-Host "==> Settings (press Enter to keep the value in [brackets])"
if (-not $cfg.github_owner -or $cfg.github_owner -eq "YOUR_GITHUB_USERNAME") {
    $cfg.github_owner = Ask "GitHub username (from github.com/USERNAME/repo)" ""
}
if (-not $cfg.github_repo) { $cfg.github_repo = Ask "Repository name" "Lab-monitor" }
if ($NewToken -or -not $cfg.token -or $cfg.token -like "github_pat_PASTE*") {
    $sec = Read-Host "Token (github_pat_..., hidden while typing)" -AsSecureString
    $cfg.token = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
if (-not $cfg.github_owner -or -not $cfg.token) { throw "Username and token are required." }

Write-Host ""
Write-Host "    Name shown on the dashboard. Dual-boot PC: use the same name in Ubuntu and Windows."
$current = if ($cfg.display_name) { $cfg.display_name } else { $env:COMPUTERNAME }
$cfg.display_name = Ask "Computer name (e.g. Lab1-PC03)" $current
Write-Host "    Lab: type 1 for Lab1, 2 for Lab2, or any other lab name."
$currentLab = if ($cfg.lab) { $cfg.lab } else { "Lab1" }
$lab = Ask "Lab" $currentLab
if ($lab -eq "1") { $lab = "Lab1" } elseif ($lab -eq "2") { $lab = "Lab2" }
$cfg.lab = $lab
foreach ($kv in ([ordered]@{ host_label = ""; sample_interval_s = 60; report_interval_s = 600; retention_days = 20 }).GetEnumerator()) {
    if (-not $cfg.Contains($kv.Key)) { $cfg[$kv.Key] = $kv.Value }
}
$utf8 = New-Object Text.UTF8Encoding $false          # no BOM
[IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json), $utf8)
Write-Host "    saved: `"$($cfg.display_name)`" in $($cfg.lab)  (repository $($cfg.github_owner)/$($cfg.github_repo))"

# ------------------------------------------------------------------ python
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python -or $python -like "*WindowsApps*") {
    $python = (& py -c "import sys; print(sys.executable)") 2>$null
}
if (-not $python -or $python -like "*WindowsApps*") { throw "No all-users Python found. Install it from python.org (see the comments at the top of this script)." }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
& $python -c "import psutil"
if ($LASTEXITCODE -ne 0) { throw "psutil missing: run '$python -m pip install psutil' as administrator." }
Write-Host "==> Using Python: $python"

# ------------------------------------------------------------------ files
function Lock($path) {   # only SYSTEM and Administrators may read it (it contains the token)
    if (Test-Path $path -PathType Container) {
        icacls $path /inheritance:r /grant:r "SYSTEM:(OI)(CI)F" "Administrators:(OI)(CI)F" /T | Out-Null
    } else {
        icacls $path /inheritance:r /grant:r "SYSTEM:F" "Administrators:F" | Out-Null
    }
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item "$src\monitor_agent.py" $InstallDir -Force
Copy-Item $cfgPath "$InstallDir\config.json" -Force
Lock "$InstallDir\config.json"                         # contains the token

# keep a copy of this folder so "Change name or lab" works later without the original files
if ((Resolve-Path $src).Path -ne $setup) {
    if (Test-Path $setup) { Remove-Item $setup -Recurse -Force }
    New-Item -ItemType Directory -Force -Path "$setup\install" | Out-Null
    Copy-Item "$src\monitor_agent.py" $setup -Force
    Copy-Item $PSCommandPath "$setup\install\install_windows.ps1" -Force
}
if ((Resolve-Path $cfgPath).Path -ne "$setup\config.json") { Copy-Item $cfgPath "$setup\config.json" -Force }
Lock $setup
$cmd = "@echo off`r`nrem Change this computer's name or lab on the lab monitor dashboard (asks for administrator rights).`r`n" +
       "powershell -NoProfile -Command `"Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -NoExit -File `"`"$setup\install\install_windows.ps1`"`"'`"`r`n"
[IO.File]::WriteAllText("$InstallDir\Change name or lab.cmd", $cmd, (New-Object Text.ASCIIEncoding))

# ------------------------------------------------------------------ scheduled tasks
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -Priority 7 -Hidden -MultipleInstances IgnoreNew
$system = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

# 1) LibreHardwareMonitor at boot (provides CPU temperature on http://localhost:8085)
if (Test-Path $LhmExe) {
    $lhmAction = New-ScheduledTaskAction -Execute $LhmExe -WorkingDirectory (Split-Path $LhmExe)
    Register-ScheduledTask -TaskName "LabMonitor-LibreHardwareMonitor" -Action $lhmAction -Trigger (New-ScheduledTaskTrigger -AtStartup) `
        -Principal $system -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "LabMonitor-LibreHardwareMonitor"
    Write-Host "==> LibreHardwareMonitor task registered"
} else {
    Write-Warning "LibreHardwareMonitor not found at $LhmExe - the agent will run but report no CPU temperature."
}

# 2) The agent itself, 30 s after boot (stop a running copy first, so a new name/lab takes effect)
Stop-ScheduledTask -TaskName "LabMonitor-Agent" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*monitor_agent.py*" } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$agentAction = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$InstallDir\monitor_agent.py`" --config `"$InstallDir\config.json`"" -WorkingDirectory $InstallDir
$agentTrigger = New-ScheduledTaskTrigger -AtStartup
$agentTrigger.Delay = "PT30S"
Register-ScheduledTask -TaskName "LabMonitor-Agent" -Action $agentAction -Trigger $agentTrigger `
    -Principal $system -Settings $settings -Force | Out-Null

Start-Sleep -Seconds 3
Write-Host "`n==> Sending a report now"
& $python "$InstallDir\monitor_agent.py" --config "$InstallDir\config.json" --test
& $python "$InstallDir\monitor_agent.py" --config "$InstallDir\config.json" --upload-now
if ($LASTEXITCODE -eq 0) { Write-Host "    uploaded - the dashboard shows it within about 10 minutes" }
else { Write-Warning "upload failed - see C:\ProgramData\LabMonitor\agent.log" }

Start-ScheduledTask -TaskName "LabMonitor-Agent"
Write-Host "`nDone. The monitor starts by itself at every boot (tasks LabMonitor-Agent and LabMonitor-LibreHardwareMonitor)."
Write-Host "To change this computer's name or lab later: double-click 'Change name or lab' in $InstallDir"
