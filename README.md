# Lab monitor

Each lab computer (Ubuntu or Windows) runs a small hidden background agent. Every 10 minutes it
uploads one report (CPU usage, CPU temperature, RAM, NVIDIA GPU load/temperature) to this repository.
GitHub Actions builds a dashboard on GitHub Pages every hour and deletes data older than 20 days every day.

```
lab PCs ──(every 10 min)──▶ branch "data": data/<machine_id>.csv ──(hourly)──▶ dashboard on GitHub Pages
                                         └──(daily)── delete reports > 20 days + reset branch history
```

The dashboard has two views:
- **Latest**: one row per computer with bars for its last report (CPU %, CPU temp, GPU temp) and a status
  (OK / Warm ≥ 80 °C / Hot ≥ 90 °C / Offline = no report for 45 min).
- **History**: a grid of small charts, one per computer, covering the last 48 h. Click a computer
  for its full 20-day page.

## Repository layout

```
agent/monitor_agent.py            the agent (Python 3.8+, needs psutil)
agent/config.example.json         copy to config.json on each PC and fill in
agent/install/install_linux.sh    Ubuntu: installs a systemd service that starts at boot
agent/install/install_windows.ps1 Windows: installs hidden scheduled tasks that start at boot
dashboard/make_plots.py           builds the website
computers.json                    optional: friendly names, and merging dual-boot ids
.github/workflows/dashboard.yml   hourly: build + publish the dashboard
.github/workflows/cleanup.yml     daily: delete reports older than 20 days, squash history
```

The `main` branch holds the code. The agents write only to the `data` branch, which they create
themselves on their first upload.

## One-time GitHub setup

1. Push these files to `main`.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Create a token for the agents: profile → Settings → Developer settings → Personal access tokens →
   **Fine-grained tokens** → Generate new token.
   - Repository access: *Only select repositories* → `lab-monitor`
   - Permissions → Repository → **Contents: Read and write** (nothing else)
   - Choose a long expiration and set a calendar reminder to renew it.
4. After the first computer has reported, go to Actions → *Build dashboard* → **Run workflow**.
   The dashboard is at `https://<your-username>.github.io/lab-monitor/`.

## Installing on a lab computer

Copy the `agent/` folder to the computer. Copy `config.example.json` to `config.json` and fill in
`github_owner` (your username) and `token`. Then test it (this uploads nothing):

```
python3 monitor_agent.py --test        # Windows: py monitor_agent.py --test
```

It prints this computer's **machine id** (for example `pc-3f9a1c0b7e`) and one reading.

### Ubuntu

```
sudo bash install/install_linux.sh
```

This installs `python3-psutil` and `lm-sensors` and creates a `labmon` system user. It then starts the
`lab-monitor` service, which runs at boot before anyone logs in, at the lowest CPU and disk priority,
capped at 5 % CPU and 100 MB RAM.
Check it with `systemctl status lab-monitor`.

### Windows

Windows has no reliable built-in CPU temperature reading, so the agent reads it from **LibreHardwareMonitor**:

1. Install Python from python.org with **"Install for all users"** ticked (not the Microsoft Store
   version), then in an admin terminal run `py -m pip install psutil`.
2. Download LibreHardwareMonitor from its GitHub releases page and unzip it to
   `C:\Program Files\LibreHardwareMonitor`. Open it once **as administrator** and enable
   **Options → Remote Web Server → Run** (port 8085). If your version has an update-interval option,
   set it to 5–10 s to lower its CPU use. Then close it.
3. In an **admin PowerShell**, from the `agent` folder:
   ```
   powershell -ExecutionPolicy Bypass -File install\install_windows.ps1
   ```

Two hidden scheduled tasks are created, `LabMonitor-LibreHardwareMonitor` and `LabMonitor-Agent`.
Both run at boot as SYSTEM, before anyone logs in, at low priority, with no window or tray icon.
To check, open `http://localhost:8085` and read the log at `C:\ProgramData\LabMonitor\agent.log`.

## Computer names and dual-boot PCs

Each computer is identified by a **machine id** taken from a hash of its wired network card's address,
not by its hostname. A dual-boot PC therefore normally gets the **same id under Ubuntu and Windows**
and appears as one computer, showing whichever OS it last reported from.

On the dashboard, a computer shows its hostname unless you give it a name in `computers.json`
(on `main`, editable directly on GitHub):

```json
{
  "pc-3f9a1c0b7e": "Lab1-PC03",
  "desktop-k2j4h5": "Lab1-PC03"
}
```

Keys can be machine ids or hostnames. Entries that share a name are merged into one computer, which
covers a dual-boot PC that ended up with two different ids. You can also set `"host_label"` in a
computer's `config.json` to force the id yourself; use the same value on both OSes.

## Resource use

Measured on Linux:
- **Agent:** about 15–25 MB RAM. It wakes once a minute for a reading that takes about 1 ms of CPU,
  and uploads a few KB every 10 minutes. Average CPU use is far below 0.1 %.
- **LibreHardwareMonitor (Windows only):** usually 50–100 MB RAM and a small, steady CPU load.
  This is the largest cost on Windows.

Why read once a minute if reports go out every 10 minutes? The 10-minute **CPU average is exact**
either way. The per-minute readings only catch short temperature peaks. To read once per report,
set `"sample_interval_s": 600`.

## Data and retention

- One CSV per computer on the `data` branch, with one row per 10-minute report. The agent drops rows
  older than `retention_days` (20) on every upload.
- The daily *Clean up old data* workflow also removes computers that haven't reported in 20 days.
  It then replaces the `data` branch history with a single commit, because deleting files alone
  wouldn't shrink a git repository. The repository stays at a few MB.
- If the network is down, reports wait on the computer (`pending.csv`) and are uploaded later.

Columns: `timestamp_utc, machine_id, hostname, os, cpu_avg, cpu_max, core_max, cpu_temp_avg,
cpu_temp_max, ram_pct, gpu_util_avg, gpu_temp_max`.

## Settings

| What | Where |
|---|---|
| Report / reading interval, retention | `config.json` on each PC (`report_interval_s`, `sample_interval_s`, `retention_days`) |
| Warm/Hot thresholds, Offline timeout, History window | top of `dashboard/make_plots.py` |
| Dashboard refresh rate | `cron` in `.github/workflows/dashboard.yml` |
| Retention on GitHub | `RETENTION_DAYS` in `.github/workflows/cleanup.yml` |

**Dashboard refresh rate:** the default is hourly. With a public repository, Actions are free, so you
can change the cron to `"*/20 * * * *"` (every 20 min; GitHub may delay scheduled runs a little).
With a private repository, stay hourly to remain inside the 2,000 free minutes per month.

GitHub disables scheduled workflows in public repositories with no activity for 60 days. If you see
a notice about that in the Actions tab, click **Enable workflow**.

## Building a Windows .exe (optional)

To avoid installing Python on every Windows PC:

```
py -m pip install pyinstaller psutil
py -m PyInstaller --onefile --noconsole --name LabMonitor monitor_agent.py
```

Put `dist\LabMonitor.exe` and `config.json` in `C:\Program Files\LabMonitor`, then point the
`LabMonitor-Agent` task's action at the .exe (no arguments needed).
