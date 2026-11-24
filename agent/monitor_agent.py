#!/usr/bin/env python3
"""
Lab monitor agent - runs in the background on each lab computer (Ubuntu or Windows).

Once a minute (sample_interval_s) it takes a quick reading: CPU usage, CPU temperature
and, if present, NVIDIA GPU load/temperature. Every 10 minutes (report_interval_s) it
turns those readings into one report row and uploads it to
    data/<machine_id>.csv      on the "data" branch of your GitHub repository
using the GitHub REST API (git is not needed). Rows older than retention_days are
dropped from the file on every upload, so each file stays small.

<machine_id> is derived from the computer's network card, so the same physical
computer gets the same id under Ubuntu and under Windows (dual boot). You can
also set "host_label" in config.json to force a name.

Only dependency outside the standard library: psutil.

Usage:
    python monitor_agent.py               # run forever (normal mode, used by the installers)
    python monitor_agent.py --test        # show machine id + one reading, upload nothing
    python monitor_agent.py --upload-now  # take a report now, upload it, exit
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import psutil

FIELDS = [
    "timestamp_utc",
    "machine_id",
    "hostname",
    "os",
    "cpu_avg",        # mean total CPU % over the report interval (exact, not sampled)
    "cpu_max",        # highest 1-minute CPU % in the interval
    "core_max",       # busiest single core % in the interval (catches one pegged core)
    "cpu_temp_avg",   # degC
    "cpu_temp_max",   # degC
    "ram_pct",        # RAM used % at report time
    "gpu_util_avg",   # %, blank if no NVIDIA GPU
    "gpu_temp_max",   # degC, blank if no NVIDIA GPU
    "display_name",   # name chosen at install time (shown on the dashboard)
    "lab",            # lab chosen at install time, e.g. "Lab1" (dashboard groups by it)
]

DEFAULTS = {
    "github_owner": "",
    "github_repo": "lab-monitor",
    "branch": "data",
    "token": "",                 # or set the LABMON_TOKEN environment variable
    "display_name": "",          # name shown on the dashboard, e.g. "Lab1-PC03" (asked by the installer)
    "lab": "",                   # lab shown on the dashboard, e.g. "Lab1" (asked by the installer)
    "host_label": "",            # optional fixed id; default: derived from the network card
    "sample_interval_s": 60,
    "report_interval_s": 600,
    "retention_days": 20,
    "lhm_url": "http://localhost:8085/data.json",  # Windows: LibreHardwareMonitor web server
    "state_dir": "",
}

IS_WINDOWS = platform.system() == "Windows"
log = logging.getLogger("labmon")


# --------------------------------------------------------------------------- config

def base_dir():
    if getattr(sys, "frozen", False):          # PyInstaller .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_state_dir():
    if IS_WINDOWS:
        return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "LabMonitor")
    if os.access("/var/lib/lab-monitor", os.W_OK):
        return "/var/lib/lab-monitor"
    return os.path.join(os.path.expanduser("~"), ".lab-monitor")


def safe_name(s):
    return re.sub(r"[^a-z0-9._-]+", "-", s.strip().lower()).strip("-") or "unknown"


def load_config(path):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8-sig") as f:   # -sig: tolerate a BOM from Windows editors
            cfg.update(json.load(f))
    cfg["token"] = os.environ.get("LABMON_TOKEN", cfg["token"])
    if not cfg["state_dir"]:
        cfg["state_dir"] = default_state_dir()
    os.makedirs(cfg["state_dir"], exist_ok=True)
    cfg["machine_id"] = safe_name(cfg["host_label"]) if cfg["host_label"] else machine_id(cfg["state_dir"])
    return cfg


def setup_logging(state_dir, verbose):
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(state_dir, "agent.log"), maxBytes=500_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if sys.stderr is not None:                 # pythonw.exe has no console
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        log.addHandler(sh)


# --------------------------------------------------------------------------- machine id

_VIRTUAL = re.compile(r"(^lo|docker|veth|^br-|virbr|vmnet|vbox|virtualbox|vmware|hyper-v|vethernet|"
                      r"^tun|^tap|^wg|^zt|tailscale|bluetooth|loopback|teredo|isatap|npcap|wan miniport)", re.I)


def physical_macs():
    """List (is_wired, mac) for real network cards, best candidates first."""
    out = []
    for name, addrs in psutil.net_if_addrs().items():
        if _VIRTUAL.search(name):
            continue
        for a in addrs:
            if a.family != psutil.AF_LINK or not a.address:
                continue
            mac = a.address.replace("-", ":").lower()
            parts = mac.split(":")
            if len(parts) != 6 or mac == "00:00:00:00:00:00":
                continue
            if int(parts[0], 16) & 0x02:       # locally administered = random/virtual MAC
                continue
            n = name.lower()
            wired = n.startswith(("en", "eth")) or ("ethernet" in n)
            out.append((not wired, mac))       # wired first, then by MAC for a stable choice
    return [mac for _, mac in sorted(out)]


def machine_id(state_dir):
    """Stable id for this physical computer: a short hash of its main network card's MAC.
    Cached in the state folder so it never changes on this installation."""
    cache = os.path.join(state_dir, "machine_id")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            mid = f.read().strip()
        if mid:
            return mid
    macs = physical_macs()
    if macs:
        mid = "pc-" + hashlib.sha256(macs[0].encode()).hexdigest()[:10]   # hashed: MAC not exposed
    else:
        mid = safe_name(socket.gethostname())
    with open(cache, "w", encoding="utf-8") as f:
        f.write(mid)
    return mid


# --------------------------------------------------------------------------- sensors

def cpu_temp_linux():
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None
    for chip, labels in (("coretemp", ("Package id 0", "Physical id 0")),
                         ("k10temp", ("Tctl", "Tdie")),
                         ("zenpower", ("Tdie", "Tctl"))):
        for entry in temps.get(chip, []):
            if entry.label in labels and entry.current:
                return float(entry.current)
    for chip in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        vals = [e.current for e in temps.get(chip, []) if e.current]
        if vals:
            return float(max(vals))
    return None


def _parse_celsius(value):
    if not isinstance(value, str) or "°C" not in value:
        return None
    try:
        return float(value.replace("°C", "").replace(",", ".").strip())
    except ValueError:
        return None


def cpu_temp_windows(lhm_url):
    """CPU temperature from LibreHardwareMonitor's web server JSON, or None."""
    try:
        with urllib.request.urlopen(lhm_url, timeout=3) as r:
            tree = json.load(r)
    except Exception as e:
        log.debug("LibreHardwareMonitor not reachable at %s: %s", lhm_url, e)
        return None
    package, others = [], []

    def walk(node, in_cpu):
        image = (node.get("ImageURL") or "").lower()
        sid = (node.get("SensorId") or "").lower()
        in_cpu = in_cpu or "cpu" in image
        is_cpu_sensor = ("cpu" in sid and "/temperature/" in sid) or (in_cpu and not sid)
        t = _parse_celsius(node.get("Value"))
        if t is not None and is_cpu_sensor and t > 0:
            text = (node.get("Text") or "").lower()
            if "package" in text or "tctl" in text or "tdie" in text:
                package.append(t)
            elif "distance" not in text:
                others.append(t)
        for child in node.get("Children", []):
            walk(child, in_cpu)

    walk(tree, False)
    return max(package) if package else (max(others) if others else None)


_NVSMI = shutil.which("nvidia-smi")
if not _NVSMI and IS_WINDOWS:
    for p in (r"C:\Windows\System32\nvidia-smi.exe",
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(p):
            _NVSMI = p
            break


def gpu_stats():
    """(mean utilisation %, max temperature degC) over NVIDIA GPUs, or (None, None)."""
    if not _NVSMI:
        return None, None
    try:
        out = subprocess.run(
            [_NVSMI, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
            creationflags=0x08000000 if IS_WINDOWS else 0,  # CREATE_NO_WINDOW: never flash a console
        ).stdout
        utils, temps = [], []
        for line in out.strip().splitlines():
            u, t = [x.strip() for x in line.split(",")[:2]]
            utils.append(float(u))
            temps.append(float(t))
        return (sum(utils) / len(utils), max(temps)) if utils else (None, None)
    except Exception as e:
        log.debug("nvidia-smi failed: %s", e)
        return None, None


class Sampler:
    """psutil.cpu_percent() measures the average since its previous call, so the CPU
    average is exact no matter how rarely we sample; sampling once a minute only
    serves to catch temperature and per-core peaks."""

    def __init__(self, cfg):
        self.cfg = cfg
        psutil.cpu_percent()                     # prime both counters
        psutil.cpu_percent(percpu=True)
        self.reset()

    def reset(self):
        self.cpu, self.core, self.temp, self.gutil, self.gtemp = [], [], [], [], []

    def sample(self):
        cores = psutil.cpu_percent(percpu=True)
        total = sum(cores) / len(cores) if cores else 0.0
        self.cpu.append(total)
        self.core.append(max(cores) if cores else 0.0)
        t = cpu_temp_windows(self.cfg["lhm_url"]) if IS_WINDOWS else cpu_temp_linux()
        if t is not None:
            self.temp.append(t)
        gu, gt = gpu_stats()
        if gu is not None:
            self.gutil.append(gu)
            self.gtemp.append(gt)
        return {"cpu": round(total, 1), "core_max": max(cores) if cores else 0.0,
                "cpu_temp": t, "gpu_util": gu, "gpu_temp": gt}

    def report(self):
        if not self.cpu:
            self.sample()
        r = lambda x: "" if x is None else f"{x:.1f}"
        avg = lambda xs: sum(xs) / len(xs) if xs else None
        row = {
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "machine_id": self.cfg["machine_id"],
            "hostname": socket.gethostname(),
            "os": "windows" if IS_WINDOWS else "linux",
            "cpu_avg": r(avg(self.cpu)),
            "cpu_max": r(max(self.cpu)),
            "core_max": r(max(self.core)),
            "cpu_temp_avg": r(avg(self.temp)),
            "cpu_temp_max": r(max(self.temp) if self.temp else None),
            "ram_pct": r(psutil.virtual_memory().percent),
            "gpu_util_avg": r(avg(self.gutil)),
            "gpu_temp_max": r(max(self.gtemp) if self.gtemp else None),
            "display_name": self.cfg.get("display_name", ""),
            "lab": self.cfg.get("lab", ""),
        }
        self.reset()
        return row


# --------------------------------------------------------------------------- local buffer

class Buffer:
    """Reports waiting to be uploaded; kept on disk so nothing is lost if the network is down."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "pending.csv")

    def append(self, row):
        new = not os.path.exists(self.path)
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

    def read(self):
        if not os.path.exists(self.path):
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def replace(self, rows):
        tmp = self.path + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- GitHub upload

class GitHub:
    API = "https://api.github.com"

    def __init__(self, cfg):
        if not cfg["github_owner"] or not cfg["token"]:
            raise RuntimeError("config.json needs github_owner and token")
        self.cfg = cfg
        self.repo = f"{self.API}/repos/{cfg['github_owner']}/{cfg['github_repo']}"
        self._branch_ok = False

    def _req(self, method, url, body=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.cfg['token']}",
            "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "lab-monitor-agent",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (payload.decode("utf-8") if raw else json.loads(payload or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, None

    def ensure_branch(self):
        """Create the data branch from the default branch the first time."""
        if self._branch_ok:
            return True
        b = self.cfg["branch"]
        status, _ = self._req("GET", f"{self.repo}/git/ref/heads/{b}")
        if status == 404:
            st, repo = self._req("GET", self.repo)
            if st != 200:
                log.warning("cannot read repository (HTTP %s) - check owner/repo/token", st)
                return False
            st, ref = self._req("GET", f"{self.repo}/git/ref/heads/{repo['default_branch']}")
            if st == 200:
                st, _ = self._req("POST", f"{self.repo}/git/refs",
                                  {"ref": f"refs/heads/{b}", "sha": ref["object"]["sha"]})
                log.info("created branch %s (HTTP %s)", b, st)
            status = 200 if st in (200, 201, 422) else st
        self._branch_ok = status == 200
        if not self._branch_ok:
            log.warning("branch check failed (HTTP %s)", status)
        return self._branch_ok

    def update_file(self, path, new_rows, retention_days):
        """Append rows to a CSV in the repo, drop rows older than retention_days. True on success."""
        if not self.ensure_branch():
            return False
        url = f"{self.repo}/contents/{path}"
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for attempt in range(4):
            status, info = self._req("GET", f"{url}?ref={self.cfg['branch']}")
            if status == 200:
                sha = info["sha"]
                if info.get("encoding") == "base64" and info.get("content"):
                    text = base64.b64decode(info["content"]).decode("utf-8")
                else:                            # files over 1 MB come without inline content
                    _, text = self._req("GET", f"{url}?ref={self.cfg['branch']}", raw=True)
                    text = text or ""
            elif status == 404:
                text, sha = "", None
            else:
                log.warning("GET %s failed with HTTP %s", path, status)
                return False

            rows = [r for r in csv.DictReader(io.StringIO(text)) if (r.get("timestamp_utc") or "") >= cutoff]
            rows += [r for r in new_rows if r["timestamp_utc"] >= cutoff]
            out = io.StringIO()
            w = csv.DictWriter(out, fieldnames=FIELDS, lineterminator="\n", extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

            body = {"message": f"{self.cfg['machine_id']}: +{len(new_rows)} report(s)",
                    "content": base64.b64encode(out.getvalue().encode("utf-8")).decode(),
                    "branch": self.cfg["branch"]}
            if sha:
                body["sha"] = sha
            status, _ = self._req("PUT", url, body)
            if status in (200, 201):
                return True
            if status in (409, 422):             # file changed meanwhile (e.g. daily cleanup) - retry
                time.sleep(3 + attempt * 5)
                continue
            log.warning("PUT %s failed with HTTP %s", path, status)
            return False
        return False


def upload(cfg, buffer):
    """Upload buffered reports. Returns True if nothing is left to send."""
    rows = buffer.read()
    if not rows:
        return True
    try:
        ok = GitHub(cfg).update_file(f"data/{cfg['machine_id']}.csv", rows, cfg["retention_days"])
    except Exception as e:
        log.warning("upload error: %s", e)
        ok = False
    if ok:
        buffer.replace([])
        log.info("uploaded %d report(s)", len(rows))
        return True
    else:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=cfg["retention_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        buffer.replace([r for r in rows if r["timestamp_utc"] >= cutoff])
        log.warning("upload failed; %d report(s) kept locally for next time", len(rows))
        return False


# --------------------------------------------------------------------------- main

def lower_priority():
    """Run at the lowest CPU and disk priority so users never notice the agent."""
    try:
        p = psutil.Process()
        if IS_WINDOWS:
            p.nice(psutil.IDLE_PRIORITY_CLASS)
        else:
            p.nice(19)
            if hasattr(p, "ionice"):
                p.ionice(psutil.IOPRIO_CLASS_IDLE)
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description="Lab monitor agent")
    ap.add_argument("--config", default=os.path.join(base_dir(), "config.json"))
    ap.add_argument("--test", action="store_true", help="show machine id and one reading, then exit")
    ap.add_argument("--upload-now", action="store_true", help="take a report now, upload it, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    setup_logging(cfg["state_dir"], args.verbose or args.test)
    buffer = Buffer(cfg["state_dir"])

    if args.test:
        s = Sampler(cfg)
        time.sleep(2)
        print("machine id :", cfg["machine_id"], "(file: data/%s.csv)" % cfg["machine_id"])
        print("name       :", cfg.get("display_name") or "(not set - dashboard will show the hostname)")
        print("lab        :", cfg.get("lab") or "(not set)")
        print("hostname   :", socket.gethostname())
        print("reading    :", s.sample())
        if IS_WINDOWS and not s.temp:
            print("NOTE: no CPU temperature - is LibreHardwareMonitor running with its web server on?")
        elif not s.temp:
            print("NOTE: no CPU temperature - try: sudo apt install lm-sensors && sudo sensors-detect --auto")
        return

    lower_priority()
    if args.upload_now:
        s = Sampler(cfg)
        time.sleep(2)
        buffer.append(s.report())
        sys.exit(0 if upload(cfg, buffer) else 1)

    log.info("agent started: id=%s host=%s os=%s gpu=%s",
             cfg["machine_id"], socket.gethostname(), platform.system(), bool(_NVSMI))
    sampler = Sampler(cfg)
    next_report = time.monotonic() + cfg["report_interval_s"]
    while True:
        time.sleep(cfg["sample_interval_s"])
        try:
            sampler.sample()
            if time.monotonic() >= next_report:
                next_report = time.monotonic() + cfg["report_interval_s"]
                buffer.append(sampler.report())
                upload(cfg, buffer)
        except Exception:
            log.exception("unexpected error (continuing)")


if __name__ == "__main__":
    main()
