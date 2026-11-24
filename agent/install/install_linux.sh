#!/usr/bin/env bash
# Install (or update) the lab monitor agent on Ubuntu.
#
#   First install, from the agent/ folder:   sudo bash install/install_linux.sh
#   Later, to change name or lab:            sudo lab-monitor-setup
#   To enter a new token (when it expires):  sudo lab-monitor-setup --token
#
# It asks for your GitHub username and token (first time only), then the computer's
# name and lab. Running it again keeps everything and only asks name and lab.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo:  sudo bash install/install_linux.sh"; exit 1; fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
SETUP=/opt/lab-monitor/setup          # installed copy of this folder, used by 'lab-monitor-setup'
CFG="$SRC/config.json"
# the installed settings are the reference: start from them if this computer is already installed
if [[ -f /opt/lab-monitor/config.json && "$CFG" != /opt/lab-monitor/config.json ]]; then
  cp /opt/lab-monitor/config.json "$CFG"
fi
NEW_TOKEN=0; [[ "${1:-}" == "--token" ]] && NEW_TOKEN=1

ask() {  # ask "prompt" "default" [secret]
  local reply
  if [[ "${3:-}" == secret ]]; then read -r -s -p "$1: " reply </dev/tty; echo >/dev/tty
  else read -r -p "$1${2:+ [$2]}: " reply </dev/tty; fi
  echo "${reply:-$2}"
}
get() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8-sig")).get(sys.argv[2],""))' "$CFG" "$1" 2>/dev/null || true; }

# ------------------------------------------------------------------ questions
echo "==> Settings (press Enter to keep the value in [brackets])"
OWNER="$(get github_owner)"; REPO="$(get github_repo)"; TOKEN="$(get token)"
NAME="$(get display_name)"; LAB="$(get lab)"
[[ "$OWNER" == YOUR_GITHUB_USERNAME ]] && OWNER=""
[[ "$TOKEN" == github_pat_PASTE* || $NEW_TOKEN == 1 ]] && TOKEN=""
[[ -n "$OWNER" ]] || OWNER="$(ask "GitHub username (from github.com/USERNAME/repo)" "")"
[[ -n "$REPO"  ]] || REPO="$(ask "Repository name" "Lab-monitor")"
[[ -n "$TOKEN" ]] || TOKEN="$(ask "Token (github_pat_..., hidden while typing)" "" secret)"
if [[ -z "$OWNER" || -z "$TOKEN" ]]; then echo "Username and token are required."; exit 1; fi

echo
echo "    Name shown on the dashboard. Dual-boot PC: use the same name in Ubuntu and Windows."
NAME="$(ask "Computer name (e.g. Lab1-PC03)" "${NAME:-$(hostname)}")"
echo "    Lab: type 1 for Lab1, 2 for Lab2, or any other lab name."
LAB="$(ask "Lab" "${LAB:-Lab1}")"
case "$LAB" in 1) LAB=Lab1 ;; 2) LAB=Lab2 ;; esac

python3 - "$CFG" "$OWNER" "$REPO" "$TOKEN" "$NAME" "$LAB" <<'PY'
import json, os, sys
path, owner, repo, token, name, lab = sys.argv[1:]
cfg = {}
if os.path.exists(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
cfg.update(github_owner=owner, github_repo=repo, token=token, display_name=name, lab=lab)
for k, v in (("host_label", ""), ("sample_interval_s", 60), ("report_interval_s", 600), ("retention_days", 20)):
    cfg.setdefault(k, v)
with open(path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
PY
chmod 600 "$CFG"
echo "    saved: \"$NAME\" in $LAB  (repository $OWNER/$REPO)"

# ------------------------------------------------------------------ packages
echo "==> Checking packages (python3, python3-psutil, lm-sensors)"
missing=()
for p in python3 python3-psutil lm-sensors; do
  dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if (( ${#missing[@]} )); then
  echo "    installing: ${missing[*]}"
  # A broken third-party source (e.g. an expired Spotify/Chrome key) makes 'apt-get update'
  # fail; that must not stop the install, so only warn.
  apt-get update -qq || echo "    WARNING: 'apt-get update' reported errors (often a third-party source); continuing."
  apt-get install -y -qq "${missing[@]}"
else
  echo "    all present"
fi
sensors-detect --auto >/dev/null 2>&1 || true

# ------------------------------------------------------------------ files
echo "==> Installing to /opt/lab-monitor"
id labmon >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin labmon
install -d -m 755 /opt/lab-monitor
install -m 755 "$SRC/monitor_agent.py" /opt/lab-monitor/
install -m 640 -o root -g labmon "$CFG" /opt/lab-monitor/config.json      # token: not world-readable
install -d -m 750 -o labmon -g labmon /var/lib/lab-monitor

# keep a copy of this folder so 'sudo lab-monitor-setup' works later without the original files
if [[ "$SRC" != "$SETUP" ]]; then
  rm -rf "$SETUP"
  install -d -m 700 "$SETUP" "$SETUP/install"
  install -m 600 "$SRC/monitor_agent.py" "$CFG" "$SETUP/"
  install -m 700 "$SRC/install/install_linux.sh" "$SETUP/install/"
fi
cat > /usr/local/bin/lab-monitor-setup <<EOF
#!/usr/bin/env bash
# Change this computer's name or lab on the lab monitor dashboard.
exec bash $SETUP/install/install_linux.sh "\$@"
EOF
chmod 755 /usr/local/bin/lab-monitor-setup

# ------------------------------------------------------------------ service
echo "==> Background service"
cat > /etc/systemd/system/lab-monitor.service <<'EOF'
[Unit]
Description=Lab monitor agent (CPU/GPU usage and temperature)
After=network-online.target
Wants=network-online.target

[Service]
User=labmon
Group=labmon
ExecStart=/usr/bin/python3 /opt/lab-monitor/monitor_agent.py --config /opt/lab-monitor/config.json
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=60
# lowest priority + hard caps, so users never notice it
Nice=19
CPUSchedulingPolicy=idle
IOSchedulingClass=idle
CPUQuota=5%
MemoryMax=100M

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable lab-monitor.service >/dev/null 2>&1
systemctl restart lab-monitor.service      # restart so a new name/lab takes effect now

echo "==> Sending a report now"
sudo -u labmon /usr/bin/python3 /opt/lab-monitor/monitor_agent.py --config /opt/lab-monitor/config.json --test || true
if sudo -u labmon /usr/bin/python3 /opt/lab-monitor/monitor_agent.py --config /opt/lab-monitor/config.json --upload-now; then
  echo "    uploaded - the dashboard shows it within about 10 minutes"
else
  echo "    upload failed - see /var/lib/lab-monitor/agent.log"
fi
echo
echo "Done. The monitor starts by itself at every boot."
echo "To change this computer's name or lab later:  sudo lab-monitor-setup"
echo "To enter a new token when it expires:          sudo lab-monitor-setup --token"
echo "Status: systemctl status lab-monitor"
