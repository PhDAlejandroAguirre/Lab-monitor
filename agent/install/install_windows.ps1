<#
Install the lab monitor agent on Windows as a scheduled task that runs at boot as SYSTEM.

Before running:
  1. Install Python from python.org with "Install for all users" ticked
     (the Microsoft Store Python is per-user and SYSTEM cannot see it).
  2. Run:  py -m pip install psutil      (in an admin terminal, so it installs for all users)
  3. Download LibreHardwareMonitor (https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases),
     unzip it to C:\Program Files\LibreHardwareMonitor, open it once as administrator and enable
     Options > Remote Web Server > Run   (port 8085). Then close it.
  4. Copy config.example.json to config.json next to monitor_agent.py and fill it in.

Then, from the agent\ folder, in an ADMIN PowerShell:
  powershell -ExecutionPolicy Bypass -File install\install_windows.ps1
#>
param(
    [string]$LhmExe = "C:\Program Files\LibreHardwareMonitor\LibreHardwareMonitor.exe",
    [string]$InstallDir = "C:\Program Files\LabMonitor"
)
$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in an administrator PowerShell."
}

$src = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path "$src\config.json")) { throw "Missing $src\config.json - copy config.example.json and fill it in." }

# Find a Python that is installed for all users
$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python -or $python -like "*WindowsApps*") {
    $python = (& py -c "import sys; print(sys.executable)") 2>$null
}
if (-not $python -or $python -like "*WindowsApps*") { throw "No all-users Python found. Install it from python.org." }
$pythonw = Join-Path (Split-Path $python) "pythonw.exe"
& $python -c "import psutil" ; if ($LASTEXITCODE -ne 0) { throw "psutil missing: run '$python -m pip install psutil' as admin." }
Write-Host "Using Python: $pythonw"

# Copy files and lock down the config (it contains the token)
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item "$src\monitor_agent.py" $InstallDir -Force
Copy-Item "$src\config.json" $InstallDir -Force
icacls "$InstallDir\config.json" /inheritance:r /grant:r "SYSTEM:F" "Administrators:F" | Out-Null

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
    -Priority 7 -Hidden -MultipleInstances IgnoreNew
$system = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$atBoot = New-ScheduledTaskTrigger -AtStartup

# 1) LibreHardwareMonitor at boot (provides CPU temperature on http://localhost:8085)
if (Test-Path $LhmExe) {
    $lhmAction = New-ScheduledTaskAction -Execute $LhmExe -WorkingDirectory (Split-Path $LhmExe)
    Register-ScheduledTask -TaskName "LabMonitor-LibreHardwareMonitor" -Action $lhmAction -Trigger $atBoot `
        -Principal $system -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName "LabMonitor-LibreHardwareMonitor"
    Write-Host "Registered LibreHardwareMonitor task."
} else {
    Write-Warning "LibreHardwareMonitor not found at $LhmExe - the agent will run but report no CPU temperature."
}

# 2) The agent itself, started 30 s after boot so LHM is ready
$agentAction = New-ScheduledTaskAction -Execute $pythonw `
    -Argument "`"$InstallDir\monitor_agent.py`" --config `"$InstallDir\config.json`"" -WorkingDirectory $InstallDir
$agentTrigger = New-ScheduledTaskTrigger -AtStartup
$agentTrigger.Delay = "PT30S"
Register-ScheduledTask -TaskName "LabMonitor-Agent" -Action $agentAction -Trigger $agentTrigger `
    -Principal $system -Settings $settings -Force | Out-Null

Start-Sleep -Seconds 5
Write-Host "`nQuick test (shows this computer's machine id):"
& $python "$InstallDir\monitor_agent.py" --config "$InstallDir\config.json" --test

Start-ScheduledTask -TaskName "LabMonitor-Agent"
Write-Host "`nDone. Tasks: LabMonitor-Agent, LabMonitor-LibreHardwareMonitor (see Task Scheduler)."
Write-Host "Log: C:\ProgramData\LabMonitor\agent.log"
