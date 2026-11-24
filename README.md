# Lab monitor

Each lab computer (Ubuntu or Windows) runs a small hidden background program, the **agent**. Every
10 minutes it uploads one report to this repository: CPU usage, CPU temperature, RAM, and NVIDIA GPU
load and temperature. GitHub Actions builds a dashboard from the reports every 10 minutes (and emails you if a computer gets too hot) and deletes
reports older than 20 days every day.

```
lab PCs ──(every 10 min)──▶ branch "data": data/<machine_id>.csv ──(rebuild)──▶ dashboard
                                         └──(daily)── delete reports > 20 days + reset branch history
```

The dashboard has two views:
- **Latest**: computers grouped by lab, one row each with bars for its last report (CPU %, CPU temp,
  GPU temp, RAM) and a status (Normal / Warm ≥ 80 °C / Hot ≥ 90 °C / Offline = no report for 45 min),
  plus a temperature strip showing every computer on one scale. Click a column header to sort.
- **History**: per lab, a grid of small charts, one per computer, covering the last 48 h. Click a
  computer for its full 20-day page.

---

## Contents

1. [What's in this repository](#1-whats-in-this-repository)
2. [One-time GitHub setup](#2-one-time-github-setup)
3. [Installing on an Ubuntu computer](#3-installing-on-an-ubuntu-computer)
4. [Installing on a Windows computer](#4-installing-on-a-windows-computer)
5. [Viewing the dashboard and the data](#5-viewing-the-dashboard-and-the-data)
6. [Computer names, labs and dual-boot PCs](#6-computer-names-labs-and-dual-boot-pcs)
7. [Email alerts for hot computers](#7-email-alerts-for-hot-computers)
8. [Troubleshooting](#8-troubleshooting)
9. [Renewing the token](#9-renewing-the-token)
10. [Reference: resources, data, settings](#10-reference)

---

## 1. What's in this repository

```
agent/monitor_agent.py            the agent (Python 3.8+, needs psutil)
agent/config.example.json         template for config.json (the installers create it for you)
agent/install/install_linux.sh    Ubuntu: installs the agent as a service that starts at boot
agent/install/install_windows.ps1 Windows: installs hidden tasks that start at boot
dashboard/make_plots.py           builds the dashboard website
dashboard/alerts.py               emails you when a computer gets too hot
computers.json                    optional: friendly names for computers
.github/workflows/dashboard.yml   every 10 minutes: build the dashboard
.github/workflows/cleanup.yml     every day: delete reports older than 20 days
```

The `main` branch holds the code. The lab computers write only to a branch called `data`, which they
create themselves on their first upload.

---

## 2. One-time GitHub setup

### 2.1 Create the token (the computers' password)

A **token** is a password made just for the lab computers. It's a long code starting with
`github_pat_` that goes in each computer's `config.json` so it can upload to this repository.
It's safer than your real password because it only works on this repository, only for reading
and writing files, and you can delete it at any time.

1. On GitHub, click your profile picture (top right) → **Settings** → **Developer settings**
   (bottom of the left menu) → **Personal access tokens** → **Fine-grained tokens** →
   **Generate new token**.
   *If you see checkboxes like `repo`, `workflow`, `gist`, you're on the "classic" token page. Go back and choose **Fine-grained tokens**.*
2. **Token name:** anything, for example `lab-monitor computers`.
3. **Expiration:** when the token expires, **every computer stops uploading** until you put a new token
   on all of them. Choose **Custom** and set a date about **one year** from today, and add a calendar
   reminder a week or two before it. ("No expiration" also works, but a leaked token then stays valid
   until you delete it.)
4. **Repository access:** select **Only select repositories** and pick **`lab-monitor`**.
   The permissions section only appears after you pick a repository.
5. **Permissions:** depending on the GitHub version you see one of these:
   - a **"+ Add permissions"** button: click it, tick **Contents**, then change it from *Read-only* to **Read and write**;
   - a collapsed **"Repository permissions"** list: open it, find **Contents** and set it to **Read and write**.

   **Metadata: Read-only** is added automatically. That's normal.
6. Click **Generate token** and **copy the token immediately**. GitHub shows it only once. Keep it in a
   password manager or private note. If you lose it, delete it and create a new one.

Use the **same token on every lab computer**. Never commit it to the repository. `config.json` is
excluded by `.gitignore` for this reason.

### 2.2 Find your GitHub username

You need it for `config.json`. It's the part of your repository's web address between
`github.com/` and `/lab-monitor`:

```
https://github.com/USERNAME/lab-monitor
```

It isn't your email address or display name. If the repository belongs to an organization, use the
organization's name instead.

---

## 3. Installing on an Ubuntu computer

### 3.1 Copy the files and open a terminal

1. Copy the `agent` folder to the computer (for example into your home folder).
2. Open a terminal **inside that folder**: in the file manager, open the `agent` folder, right-click an
   empty area and choose **Open in Terminal**. You can also press **Ctrl+Alt+T** and type
   `cd ~/agent`, using whatever path you copied it to.

On Ubuntu the commands are `python3` and `apt`. **`py` is a Windows command.** If Ubuntu suggests
installing `pythonpy`, don't; it's unrelated.

### 3.2 Install it

From the `agent` folder:

```
sudo bash install/install_linux.sh
```

(Type exactly that. Running `install/install_linux.sh` without `sudo bash` gives *Permission denied*.)

It asks a few questions. Press Enter to accept a value shown in [brackets]:

```
GitHub username (from github.com/USERNAME/repo): PhDAlejandroAguirre
Repository name [Lab-monitor]:
Token (github_pat_..., hidden while typing):          <- paste the token from 2.1, then Enter
Computer name (e.g. Lab1-PC03) [alejo]: Lab1-PC03
Lab [Lab1]: 1                                         <- 1 = Lab1, 2 = Lab2, or type another lab name
```

- Username and token are asked only the first time.
- The **name** and **lab** are what the dashboard shows; computers are grouped by lab.
- For a **dual-boot PC**, type the **same name and lab** in Ubuntu and in Windows, and it appears as
  one computer.
- Nothing appears while you paste the token. That's normal.

The installer then:
- installs `python3-psutil` and `lm-sensors` if they're missing (a broken third-party software source,
  e.g. an expired Spotify key, no longer stops it);
- installs the `lab-monitor` service, which starts **automatically at every boot**, before anyone logs
  in, is **invisible to users**, and runs at the lowest priority (max 5 % CPU, 100 MB RAM);
- installs the command `lab-monitor-setup`, for changing the name or lab later (3.3);
- prints this computer's machine id and a reading, then **sends a first report**.

The computer appears on the dashboard at the next dashboard build (or right away if you run
*Build dashboard* from the Actions tab).

Check the service with `systemctl status lab-monitor` (it should say *active (running)*; press `q` to exit).
Logs: `sudo tail -5 /var/lib/lab-monitor/agent.log` (the last 5 lines; a healthy one says
`uploaded 1 report(s)` every 10 minutes) or `journalctl -u lab-monitor -f`. After installing you can delete the copied folder; the installed
copy lives in `/opt/lab-monitor`.

### 3.3 Renaming a computer or moving it to another lab

On the computer, open a terminal and run:

```
sudo lab-monitor-setup
```

It asks only for the name and the lab (Enter keeps the current value), restarts the monitor and sends a
report, so the dashboard shows the change within about 10 minutes. The computer keeps its history.

You can also rename computers or change their lab from GitHub without touching them (section 6).

**Updating the agent** after a new version is pushed to GitHub: copy the new `agent` folder to the
computer and run `sudo bash install/install_linux.sh` from it again. It keeps the settings.

### 3.4 Testing without installing (optional)

```
sudo apt install python3-psutil lm-sensors
cp config.example.json config.json && nano config.json     # fill in github_owner, token, display_name, lab
python3 monitor_agent.py --test          # machine id + one reading, uploads nothing
python3 monitor_agent.py --upload-now    # sends one report; check the "data" branch on GitHub
```

---

## 4. Installing on a Windows computer

Windows has no reliable built-in way to read CPU temperature, so the agent gets it from a free
program, **LibreHardwareMonitor**.

### 4.1 Install Python and LibreHardwareMonitor (once per computer)

1. Download Python from **python.org**. In the installer, tick **"Add python.exe to PATH"**, choose
   **Customize installation**, and tick **"Install Python for all users"**.
   *Don't use the Microsoft Store version. The background task can't see it.*
2. Download LibreHardwareMonitor from its GitHub releases page and unzip it to
   `C:\Program Files\LibreHardwareMonitor`.
3. Right-click `LibreHardwareMonitor.exe` → **Run as administrator**. Enable
   **Options → Remote Web Server → Run** (port 8085). If your version has an update-interval option,
   set it to 5–10 s to lower its CPU use. Close the program; the setting is saved.

### 4.2 Copy the files and open a terminal

1. Copy the `agent` folder to the computer (for example to the Desktop).
2. Open an **administrator** terminal: Start menu → type `PowerShell` → right-click
   **Windows PowerShell** → **Run as administrator**. Then go to the folder, for example:
   ```
   cd $HOME\Desktop\agent
   ```

On Windows the Python command is **`py`**, not `python3`.

### 4.3 Install it

In the administrator PowerShell, from the `agent` folder:

```
py -m pip install psutil
powershell -ExecutionPolicy Bypass -File install\install_windows.ps1
```

It asks the same questions as on Ubuntu (username, token, name, lab; see 3.2). For a dual-boot PC,
use the same name and lab as in Ubuntu. It then prints the machine id and a reading and sends a first report.

**Renaming the computer or moving it to another lab later:** open `C:\Program Files\LabMonitor` and
double-click **Change name or lab**. Windows asks for administrator permission; then it asks only for the
name and lab (Enter keeps the current value). The computer keeps its history.

It creates two scheduled tasks, `LabMonitor-LibreHardwareMonitor` and `LabMonitor-Agent`. They:
- start **automatically at every boot** as SYSTEM, before anyone logs in;
- are **invisible to users**: no window, console flash or tray icon;
- run at low priority.

`cpu_temp` in the test reading is `None` if LibreHardwareMonitor isn't running yet. The task starts it,
and from the next boot on it's always running.

To check it's working, restart the computer, open `http://localhost:8085` in a browser
(LibreHardwareMonitor's sensor list should load), and look at the log
`C:\ProgramData\LabMonitor\agent.log`.

---

## 5. Viewing the dashboard and the data

### The raw data (works on any plan)

On GitHub, click the branch dropdown (it says **main**), select **data**, and open the `data` folder.
There's one CSV per computer, and GitHub shows it as a table. New rows arrive every 10 minutes.

### The dashboard as a downloadable zip (works on any plan, including private + free)

The *Build dashboard* workflow runs every 10 minutes.

1. Open the **Actions** tab and click **Build dashboard** on the left.
   *The first time, click **Run workflow** → **Run workflow** so you don't have to wait.*
2. Click the most recent run that has a green check mark.
3. At the bottom, under **Artifacts**, click **lab-dashboard** to download a zip.
4. Unzip it and double-click **index.html**. It opens in your browser with the *Latest* and *History* tabs.

Only people with access to the repository can download it. Each zip is kept for one day, and a new one
is made every 10 minutes.

### The dashboard as a website (GitHub Pages)

| Situation | Website available? |
|---|---|
| **Public** repository (free) | Yes |
| **Private** repository + GitHub **Pro** or **Team** (paid, or free with GitHub Education) | Yes |
| **Private** repository on the **free** plan | No, so use the zip above |

A GitHub Pages website is **visible to anyone who has its link**, even when the repository is private.
Only the code and raw data stay private.

To turn it on, once your plan allows it:
1. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. Settings → Secrets and variables → Actions → **Variables** tab → **New repository variable**:
   name `USE_PAGES`, value `true`.
3. Actions → *Build dashboard* → **Run workflow**. The site appears at
   `https://<your-username>.github.io/lab-monitor/` and updates every 10 minutes.

**Check whether you have GitHub Education:** go to `github.com/settings/billing/summary#education-benefits`.
Teachers and researchers can apply there for free GitHub Team (for an organization). Students can get
free GitHub Pro. Both need a university email address and proof of affiliation.

---

## 6. Computer names, labs and dual-boot PCs

Each computer is identified by a **machine id** (like `pc-3f9a1c0b7e`) made from a hash of its wired
network card's address, not by its hostname. A dual-boot PC therefore normally gets the **same id
under Ubuntu and Windows** and appears as one computer, showing whichever OS it last reported from.

The dashboard groups computers **by lab**, and shows the lab as a colored tag beside every computer name.

**The name and lab** come from, in order of priority:
1. `computers.json` on `main`, if it lists the computer;
2. the name and lab typed in the installer (change them with `sudo lab-monitor-setup` on Ubuntu or
   *Change name or lab* on Windows; see 3.3 and 4.3);
3. the computer's hostname, in a group called **Unassigned**.

Computers with the same name are shown as one (handy for dual-boot PCs).

**To rename or move a computer from GitHub** without touching it, edit `computers.json` on `main`
(pencil icon). Find the machine id on the computer's dashboard page, or as the file name in the `data` branch.
A value can be just a name, or a name and a lab:

```json
{
  "pc-3f9a1c0b7e": "Lab1-PC03",
  "pc-8d21ab47c0": {"name": "Lab2-PC04", "lab": "Lab2"}
}
```

Put a comma after every entry except the last. Keys can also be hostnames. An entry here wins over what
was typed in the installer, so delete the entry if you'd rather manage that computer from the installer.
To see the change right away, run *Build dashboard* from the Actions tab.

---

## 7. Email alerts for hot computers

Every time the dashboard is built (every 10 minutes), it checks each computer's latest report and emails you:
- **when a computer goes over the limit**: CPU or GPU at or above **90 °C** by default;
- **a reminder every 6 hours** while it stays hot;
- **"back to normal"** once it drops 5 °C below the limit, or if it stops reporting while hot.

All computers that changed go into one email, so you never get an email every 10 minutes.
Example subject: *🔥 Lab monitor – HOT: Lab1-PC03*.

Alerts are off until you complete the setup below. The emails are sent from a Gmail account.
**We recommend creating a separate Gmail account for the lab** (for example `labmonitor.cfd@gmail.com`)
rather than using your personal one.

### 7.1 Create a Gmail app password

Gmail doesn't let programs use your normal password. You create a special 16-character
**app password** instead:

1. Sign in to the Gmail account that will send the emails.
2. Turn on **2-Step Verification**: Google Account → **Security** → 2-Step Verification. App passwords
   don't exist without it.
3. Go to **myaccount.google.com/apppasswords**, type a name (`lab-monitor`) and click **Create**.
4. Copy the 16-character password it shows (the spaces don't matter). Like the token, it's shown only once.

### 7.2 Store it in GitHub (hidden)

In your repository: **Settings → Secrets and variables → Actions**.

**Secrets** tab → **New repository secret**, twice:

| Name | Value |
|---|---|
| `SMTP_USER` | the Gmail address, e.g. `labmonitor.cfd@gmail.com` |
| `SMTP_PASSWORD` | the 16-character app password |

Secrets are encrypted and never shown, not even in a public repository.

**Variables** tab → **New repository variable** (all optional):

| Name | Value | Default if not set |
|---|---|---|
| `ALERT_TO` | who receives the alerts; several addresses separated by commas | the `SMTP_USER` address |
| `ALERT_TEMP` | limit in °C, e.g. `85` | `90` |
| `ALERT_REPEAT_HOURS` | reminder interval while still hot | `6` |

### 7.3 Send a test email

Actions → **Build dashboard** → **Run workflow**, tick **"Send a test email"** → **Run workflow**.
Within a minute or two you should receive *✅ Lab monitor – test email*. Check the spam folder the first
time and mark it "Not spam".

If it doesn't arrive, open the run and look at the step *Check temperatures and email if needed*.
**Username and Password not accepted** means the app password is wrong, or 2-Step Verification is off.
A mail problem never stops the dashboard from updating.

**Using a university email to send instead of Gmail:** also set the variables `SMTP_HOST` and
`SMTP_PORT`, for example `smtp.office365.com` and `587` for Microsoft 365. Many universities block this,
so Gmail is the reliable option.

---

## 8. Troubleshooting

### 8.1 "Last report" is old, but the computer is on

Either the computer isn't sending, or GitHub isn't rebuilding the dashboard. Find out which:

**Step 1: look at "Updated" at the top right of the dashboard.**
- If **"Updated" is also old**, the computer may be fine; GitHub isn't rebuilding. Go to step 2.
- If **"Updated" is recent** but "Last report" is old, the computer isn't sending. Go to step 3.

**Step 2: check GitHub.** Open the **Actions** tab and look at the *Build dashboard* runs:
- **Red ✗:** click the newest run, then the red step, and read the error.
- **No runs for a while:** the computers may not be triggering rebuilds (see "How the dashboard stays
  up to date" in section 10), or none are reporting (step 3). Click **Run workflow** to update now.
- **Green runs every 10 minutes:** the browser is showing an old copy. Press **Ctrl + Shift + R**.

**Step 3: check the computer.**

*Ubuntu*, in a terminal:

```
systemctl status lab-monitor                  # should say "active (running)"; press q to exit
sudo tail -5 /var/lib/lab-monitor/agent.log   # last 5 log lines
```

A healthy log shows `uploaded 1 report(s)` about every 10 minutes (local time). Then:
- If the **service isn't running**, start it: `sudo systemctl restart lab-monitor`.
- If you see **`upload failed`** with HTTP 401, the token is wrong or expired; renew it (section 9).
  Without an HTTP code, the computer has no internet. Reports are kept and sent later.
- **More detail:** `journalctl -u lab-monitor -n 50`.

*Windows:* open `C:\ProgramData\LabMonitor\agent.log` with Notepad and look at the last lines (same
messages as above). In Task Scheduler (tick **View → Show Hidden Tasks**, since the tasks are hidden),
the task `LabMonitor-Agent` should be *Running*. If it isn't, right-click it and choose **Run**.

### 8.2 Common errors

| Problem | Fix |
|---|---|
| `No module named 'psutil'` | Ubuntu: `sudo apt install python3-psutil`. Windows: `py -m pip install psutil` |
| `py: command not found` (Ubuntu) | `py` is for Windows. On Ubuntu use `python3` and `apt` |
| `python3 is not recognized` (Windows) | On Windows use `py` |
| `'py' is not recognized` (Windows) | Python isn't installed, or was installed without "Add to PATH". Reinstall from python.org (4.1) |
| `install/install_linux.sh: Permission denied` | Run it as `sudo bash install/install_linux.sh` |
| `apt-get update` errors about Spotify, Chrome or another source | Not a problem for the installer anymore. To silence it: `sudo rm /etc/apt/sources.list.d/spotify.list` (or that source's file) |
| Error mentioning **JSON** | A quotation mark or comma is missing in `config.json` or `computers.json` |
| `cpu_temp: None` on Ubuntu | `sudo apt install lm-sensors` then `sudo sensors-detect --auto` |
| `cpu_temp: None` on Windows | LibreHardwareMonitor isn't running or its web server is off. Check `http://localhost:8085` (4.1) |
| `--upload-now` doesn't create the `data` branch | Check the log (`~/.lab-monitor/agent.log` when testing on Ubuntu, `C:\ProgramData\LabMonitor\agent.log` on Windows). **HTTP 401**: wrong or expired token. **HTTP 404**: wrong username or repository name, or the token doesn't include this repository. **HTTP 403**: the token lacks *Contents: Read and write* |
| A computer shows **Offline** | It's off, has no internet, or the token expired. See 8.1 |
| The dashboard stopped updating | See 8.1, step 2. If GitHub shows a notice that the scheduled workflow was disabled for inactivity, click **Enable workflow** |

---

## 9. Renewing the token

Before the token expires, create a new one (2.1). Then, on each computer:

- **Ubuntu:** `sudo lab-monitor-setup --token`, paste the new token, and press Enter at the name and lab
  questions to keep them.
- **Windows:** in an administrator PowerShell:
  ```
  powershell -ExecutionPolicy Bypass -File "C:\Program Files\LabMonitor\setup\install\install_windows.ps1" -NewToken
  ```

No data is lost if you're a few days late. Each computer keeps its reports locally while uploads
fail and sends them once the new token is in place. Reports older than 20 days are dropped.

---

## 10. Reference

### Resource use

Measured on Linux:
- **Agent:** about 15–25 MB RAM. It wakes once a minute for a reading that takes about 1 ms of CPU,
  and uploads a few KB every 10 minutes. Average CPU use is far below 0.1 %.
- **LibreHardwareMonitor (Windows only):** usually 50–100 MB RAM and a small, steady CPU load.

The agent takes a reading once a minute but reports every 10 minutes. The 10-minute **CPU average is
exact** either way. The per-minute readings only catch short temperature peaks. To read once per
report, set `"sample_interval_s": 600`.

### How the dashboard stays up to date

GitHub's own timer (`schedule` in `dashboard.yml`) is unreliable: GitHub delays scheduled runs and often
skips them entirely, especially on new or quiet repositories. So after each upload, a computer also
**asks GitHub to rebuild the dashboard** (a `repository_dispatch` event called `report`, allowed by the same
token). It skips this if a rebuild started less than 8 minutes ago, so many computers reporting at once
cause roughly one rebuild every 10 minutes. These runs appear in the Actions tab as *Build dashboard*
with the event `repository_dispatch`. The 10-minute schedule stays as a backup.

To turn it off on a computer, set `"trigger_dashboard": false` in its config.

### Data and retention

- One CSV per computer on the `data` branch, with one row per 10-minute report. Each computer drops
  its rows older than `retention_days` (20) on every upload.
- The daily *Clean up old data* workflow also removes computers that haven't reported in 20 days.
  It then replaces the `data` branch history with a single commit, because deleting files alone
  wouldn't shrink a git repository. The repository stays at a few MB.
- Columns: `timestamp_utc, machine_id, hostname, os, cpu_avg, cpu_max, core_max, cpu_temp_avg,
  cpu_temp_max, ram_pct, gpu_util_avg, gpu_temp_max, display_name, lab`. Times in the files are UTC; the dashboard shows Chile time.

### Settings

| What | Where |
|---|---|
| Report / reading interval, retention | `config.json` on each PC (`report_interval_s`, `sample_interval_s`, `retention_days`) |
| Warm/Hot colors on the dashboard, Offline timeout, History window, time zone | top of `dashboard/make_plots.py` |
| Email alert limit, recipients, reminder interval | repository variables `ALERT_TEMP`, `ALERT_TO`, `ALERT_REPEAT_HOURS` (section 7) |
| Dashboard refresh rate | `cron` in `.github/workflows/dashboard.yml` |
| Retention on GitHub | `RETENTION_DAYS` in `.github/workflows/cleanup.yml` |

**Dashboard refresh rate:** the default is every 10 minutes (`"3-59/10 * * * *"`), which is free for public
repositories. GitHub sometimes starts scheduled runs a few minutes late, so expect updates every 10–15 min.
With a private repository on the free plan, use `"7 * * * *"` (hourly) to stay inside the 2,000 free
Actions minutes per month.

### Agent commands

```
python3 monitor_agent.py --test         # show machine id + one reading, upload nothing
python3 monitor_agent.py --upload-now   # take one report and upload it now
python3 monitor_agent.py -v             # run in the foreground with detailed logging
```

(On Windows replace `python3` with `py`.)

### Building a Windows .exe (optional)

To avoid installing Python on every Windows PC:

```
py -m pip install pyinstaller psutil
py -m PyInstaller --onefile --noconsole --name LabMonitor monitor_agent.py
```

Put `dist\LabMonitor.exe` and `config.json` in `C:\Program Files\LabMonitor`, then point the
`LabMonitor-Agent` task's action at the .exe (no arguments needed).
