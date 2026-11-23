#!/usr/bin/env bash
# Install the lab monitor agent as a systemd service on Ubuntu.
# Run from the agent/ folder:   sudo bash install/install_linux.sh
# Put your filled-in config.json next to monitor_agent.py first.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo"; exit 1; fi
SRC="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -f "$SRC/config.json" ]]; then
  echo "Missing $SRC/config.json - copy config.example.json to config.json and fill it in."; exit 1
fi

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

echo "==> Creating service user 'labmon'"
id labmon >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin labmon

echo "==> Copying files to /opt/lab-monitor"
install -d -m 755 /opt/lab-monitor
install -m 755 "$SRC/monitor_agent.py" /opt/lab-monitor/
install -m 640 -o root -g labmon "$SRC/config.json" /opt/lab-monitor/config.json   # token: not world-readable
install -d -m 750 -o labmon -g labmon /var/lib/lab-monitor

echo "==> Creating systemd service"
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
systemctl enable --now lab-monitor.service

echo "==> Quick test:"
sudo -u labmon /usr/bin/python3 /opt/lab-monitor/monitor_agent.py --config /opt/lab-monitor/config.json --test || true
echo
echo "Done. Check status with:  systemctl status lab-monitor"
echo "Logs:                      journalctl -u lab-monitor -f   (also /var/lib/lab-monitor/agent.log)"
