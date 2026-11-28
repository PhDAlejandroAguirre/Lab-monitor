#!/usr/bin/env python3
"""
Build the static dashboard from data/<machine_id>.csv (one file per computer).

Output in --out (default "site/"):
    index.html     LATEST view: bar chart of each computer's last report + status
    history.html   HISTORY view: a grid of small charts, one per computer (last 48 h)
    pc-*.html      detail page per computer (full retention period)
    img/*.png      the charts

Computer names come from computers.json (repo root) when present:
    { "pc-1a2b3c4d5e": "Lab1-PC03", "lab2-pc07-win": "Lab2-PC07" }
Keys can be a machine id or a hostname. Several ids mapped to the same name are merged
into one computer (useful for dual-boot machines whose ids differ between OSes).
Unmapped computers are shown by their hostname.
"""

import argparse
import glob
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

# ---- settings you may want to change
CARD_HOURS = 48                      # time window of the small charts in the History view
STALE_AFTER = timedelta(minutes=45)  # "Offline" if no report for this long
TEMP_WARN, TEMP_CRIT = 80, 90        # degC

# ---- palette (validated categorical slots 1-2, neutrals, status)
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8984", "#e6e5e0", "#fcfcfb"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
    "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": INK2, "ytick.color": INK2, "font.size": 8,
    "legend.frameon": False, "lines.linewidth": 1.5,
})

NUM = ["cpu_avg", "cpu_max", "core_max", "cpu_temp_avg", "cpu_temp_max", "ram_pct", "gpu_util_avg", "gpu_temp_max"]


# --------------------------------------------------------------------------- data

def load(data_dir, names_file):
    names = {}
    if names_file and os.path.exists(names_file):
        with open(names_file, encoding="utf-8") as f:
            names = {k.lower(): v for k, v in json.load(f).items() if not k.startswith("_")}
    frames = []
    for f in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        try:
            d = pd.read_csv(f, dtype={"machine_id": str, "hostname": str, "os": str})
        except Exception as e:
            print(f"skipping {f}: {e}")
            continue
        if d.empty:
            continue
        d["file_id"] = os.path.splitext(os.path.basename(f))[0]
        frames.append(d)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["timestamp_utc"] = pd.to_datetime(df["timestamp_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["timestamp_utc"]).sort_values("timestamp_utc")
    for c in NUM:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")

    # Resolve a display name per file: computers.json (by id, then hostname) else latest hostname
    latest_host = df.groupby("file_id")["hostname"].last().fillna("unknown")
    display = {}
    for fid, host in latest_host.items():
        display[fid] = names.get(fid.lower()) or names.get(str(host).lower())
    unnamed = {fid: str(latest_host[fid]) for fid, v in display.items() if not v}
    counts = pd.Series(list(unnamed.values())).value_counts() if unnamed else {}
    for fid, host in unnamed.items():   # two different PCs both called "ubuntu"? keep them apart
        display[fid] = host if counts[host] == 1 else f"{host} ({fid[-4:]})"
    df["name"] = df["file_id"].map(display)
    return df


def page_id(name):
    return "pc-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x")


def resample(d, freq):
    """Bin the reports (mean for averages, max for maxima). Empty bins become NaN,
    so lines break where the computer was off instead of bridging the gap."""
    agg = {c: ("max" if c.endswith("max") else "mean") for c in
           ["cpu_avg", "core_max", "cpu_temp_max", "ram_pct", "gpu_util_avg", "gpu_temp_max"]}
    return d.set_index("timestamp_utc")[list(agg)].resample(freq).agg(agg)


def status(last_seen, temp, now):
    if now - last_seen > STALE_AFTER:
        return "off", "○ Offline"
    if pd.notna(temp) and temp >= TEMP_CRIT:
        return "crit", "▲ Hot"
    if pd.notna(temp) and temp >= TEMP_WARN:
        return "warn", "● Warm"
    return "ok", "✓ OK"


# --------------------------------------------------------------------------- charts

def time_axis(ax, hours):
    if hours <= 72:
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    else:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=max(1, hours // 24 // 8)))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))


def small_card(d, now, path):
    """Compact chart for the History grid: CPU % on top, temperature below."""
    start = now - timedelta(hours=CARD_HOURS)
    r = resample(d[d["timestamp_utc"] >= start], "10min")
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(3.9, 2.5), sharex=True, gridspec_kw={"hspace": 0.35})
    a1.plot(r["cpu_avg"], color=BLUE)
    a1.set_ylim(0, 105)
    a1.set_yticks([0, 50, 100])
    a1.set_title("CPU %", fontsize=8, color=INK2, fontweight="normal")
    has_gpu = r["gpu_temp_max"].notna().any()
    a2.plot(r["cpu_temp_max"], color=BLUE, label="CPU")
    if has_gpu:
        a2.plot(r["gpu_temp_max"], color=ORANGE, label="GPU")
    a2.axhline(TEMP_CRIT, color=MUTED, linewidth=0.7, linestyle="--")
    a2.set_ylim(20, 100)
    a2.set_yticks([20, 60, 100])
    a2.set_title("Temperature °C", fontsize=8, color=INK2, fontweight="normal")
    if has_gpu:  # tiny legend on the title row
        a2.legend(loc="lower right", bbox_to_anchor=(1, 0.98), ncol=2, fontsize=7, handlelength=1.2,
                  borderaxespad=0, columnspacing=0.8)
    for a in (a1, a2):
        a.set_xlim(start, now)
        a.tick_params(labelsize=7)
    time_axis(a2, CARD_HOURS)
    fig.subplots_adjust(left=0.1, right=0.98, top=0.9, bottom=0.17)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def detail_figure(d, name, days, path):
    r = resample(d, "1h" if days > 3 else "10min")
    has_gpu = r["gpu_temp_max"].notna().any()
    fig, axes = plt.subplots(3, 1, figsize=(10, 7), sharex=True)

    ax = axes[0]
    ax.plot(r["cpu_avg"], color=BLUE, label="CPU average")
    ax.plot(r["core_max"], color=MUTED, linewidth=0.9, label="Busiest core")
    ax.set_ylim(0, 105)
    ax.set_ylabel("%")
    ax.set_title("CPU usage")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=2)

    ax = axes[1]
    ax.plot(r["cpu_temp_max"], color=BLUE, label="CPU")
    if has_gpu:
        ax.plot(r["gpu_temp_max"], color=ORANGE, label="GPU")
    ax.axhline(TEMP_CRIT, color=MUTED, linewidth=0.8, linestyle="--")
    ax.text(1.0, TEMP_CRIT, f" {TEMP_CRIT} °C", transform=ax.get_yaxis_transform(), color=INK2, va="center", fontsize=8)
    ax.set_ylabel("°C")
    ax.set_title("Temperature (max)")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=2)

    ax = axes[2]
    ax.plot(r["ram_pct"], color=AQUA, label="RAM used")
    if has_gpu:
        ax.plot(r["gpu_util_avg"], color=ORANGE, label="GPU load")
    ax.set_ylim(0, 105)
    ax.set_ylabel("%")
    ax.set_title("Memory and GPU load")
    ax.legend(loc="lower right", bbox_to_anchor=(1, 1), ncol=2)

    time_axis(axes[-1], days * 24)
    fig.suptitle(f"{name}  ·  times in UTC", x=0.01, ha="left", color=INK2, fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------------- HTML

CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#fcfcfb;color:#0b0b0b;margin:0 auto;padding:20px 24px;max-width:1300px}
h1{font-size:22px;margin:0 0 4px} .sub{color:#52514e;margin:0 0 16px;font-size:13px}
nav{display:flex;gap:4px;margin:0 0 16px;border-bottom:1px solid #e6e5e0}
nav a{padding:8px 14px;color:#52514e;text-decoration:none;border-bottom:2px solid transparent;font-weight:600;font-size:14px}
nav a.on{color:#0b0b0b;border-color:#2a78d6}
a{color:#2a78d6;text-decoration:none} a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #eeede8;white-space:nowrap}
th{color:#52514e;font-weight:600;font-size:12px}
.barcell{width:24%} .bw{display:flex;align-items:center;gap:8px}
.bar{flex:1;height:12px;background:#f1f0ec;border-radius:0 4px 4px 0;position:relative}
.bar span{position:absolute;left:0;top:0;bottom:0;border-radius:0 4px 4px 0;background:#2a78d6}
.bar i{position:absolute;top:-3px;bottom:-3px;width:1px;background:#8a8984}
.bar.off span{background:#c9c8c2} .bar span.warn{background:#eda100} .bar span.crit{background:#e34948}
.v{width:52px;text-align:right;font-variant-numeric:tabular-nums;color:#0b0b0b}
.muted{color:#8a8984}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}
.ok{background:#e3f4e8;color:#0b6b2b}.warn{background:#fff1d6;color:#8a5a00}.crit{background:#fde2e1;color:#a51d1c}.off{background:#eeedea;color:#52514e}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
.card{border:1px solid #e6e5e0;border-radius:8px;padding:8px 10px 4px;background:#fcfcfb}
.card .hd{display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;gap:6px}
.card .hd a{font-weight:700;color:#0b0b0b;font-size:14px;overflow:hidden;text-overflow:ellipsis}
.card .meta{font-size:11px;color:#52514e;margin-bottom:2px}
.card img{width:100%;height:auto;display:block}
img.big{max-width:100%;border:1px solid #e6e5e0;border-radius:6px}
"""


def page(title, body):
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<meta http-equiv='refresh' content='600'><title>{html.escape(title)}</title>"
            f"<style>{CSS}</style></head><body>{body}</body></html>")


def nav(active):
    tabs = [("index.html", "Latest"), ("history.html", "History")]
    return "<nav>" + "".join(f"<a href='{h}' class='{'on' if t == active else ''}'>{t}</a>" for h, t in tabs) + "</nav>"


def ago(delta):
    m = int(delta.total_seconds() // 60)
    if m < 60:
        return f"{m} min ago"
    if m < 48 * 60:
        return f"{m // 60} h ago"
    return f"{m // 1440} days ago"


def bar(value, vmax, unit, cls="", ticks=(), off=False):
    if pd.isna(value):
        return "<div class='bw'><div class='bar'></div><span class='v muted'>–</span></div>"
    w = max(0.0, min(100.0, value / vmax * 100))
    t = "".join(f"<i style='left:{x / vmax * 100:.1f}%'></i>" for x in ticks)
    return (f"<div class='bw' title='{value:.0f}{unit}'><div class='bar{' off' if off else ''}'>"
            f"<span class='{cls}' style='width:{w:.1f}%'></span>{t}</div>"
            f"<span class='v{' muted' if off else ''}'>{value:.0f}{unit}</span></div>")


def temp_cls(t):
    return "crit" if pd.notna(t) and t >= TEMP_CRIT else ("warn" if pd.notna(t) and t >= TEMP_WARN else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--names", default="computers.json")
    ap.add_argument("--out", default="site")
    ap.add_argument("--days", type=int, default=20, help="history shown on detail pages")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, "img"), exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    df = load(a.data, a.names)
    if not df.empty:
        df = df[df["timestamp_utc"] >= now - timedelta(days=a.days)]

    if df.empty:
        for f, t in (("index.html", "Latest"), ("history.html", "History")):
            with open(os.path.join(a.out, f), "w", encoding="utf-8") as fh:
                fh.write(page("Lab monitor", f"<h1>Lab monitor</h1>{nav(t)}<p class='sub'>No reports yet · updated {stamp}</p>"))
        print("no data")
        return

    any_gpu = df["gpu_temp_max"].notna().any()
    rows, cards = [], []
    for name in sorted(df["name"].unique(), key=lambda s: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", s)]):
        d = df[df["name"] == name]
        pid = page_id(name)
        h = html.escape(name)
        last = d.iloc[-1]
        seen = last["timestamp_utc"]
        cls, label = status(seen, last["cpu_temp_max"], now)
        off = cls == "off"
        osname = html.escape(str(last["os"]).capitalize())
        when = ago(now - seen)

        # LATEST view row
        rows.append(
            f"<tr><td><a href='{pid}.html'><b>{h}</b></a></td><td>{osname}</td>"
            f"<td><span class='badge {cls}'>{label}</span></td>"
            f"<td class='barcell'>{bar(last['cpu_avg'], 100, '%', off=off)}</td>"
            f"<td class='barcell'>{bar(last['cpu_temp_max'], 100, ' °C', '' if off else temp_cls(last['cpu_temp_max']), (TEMP_WARN, TEMP_CRIT), off)}</td>"
            + (f"<td class='barcell'>{bar(last['gpu_temp_max'], 100, ' °C', '' if off else temp_cls(last['gpu_temp_max']), (TEMP_WARN, TEMP_CRIT), off)}</td>" if any_gpu else "")
            + f"<td class='muted'>{when}</td></tr>")

        # HISTORY view card + detail page
        small_card(d, now, os.path.join(a.out, "img", f"{pid}_card.png"))
        cards.append(
            f"<div class='card'><div class='hd'><a href='{pid}.html'>{h}</a><span class='badge {cls}'>{label}</span></div>"
            f"<div class='meta'>{osname} · last report {when}</div>"
            f"<a href='{pid}.html'><img src='img/{pid}_card.png' alt='Last {CARD_HOURS} hours for {h}' loading='lazy'></a></div>")
        detail_figure(d, name, a.days, os.path.join(a.out, "img", f"{pid}.png"))
        ids = ", ".join(sorted(d["file_id"].unique()))
        with open(os.path.join(a.out, f"{pid}.html"), "w", encoding="utf-8") as fh:
            fh.write(page(f"{name} - Lab monitor",
                          f"<p><a href='index.html'>← Latest</a> · <a href='history.html'>History</a></p><h1>{h}</h1>"
                          f"<p class='sub'>{osname} · last report {seen.strftime('%Y-%m-%d %H:%M UTC')} · "
                          f"last {a.days} days · machine id: {html.escape(ids)}</p>"
                          f"<img class='big' src='img/{pid}.png' alt='Usage and temperature history for {h}'>"))

    n = df["name"].nunique()
    head = (f"<tr><th>Computer</th><th>OS</th><th>Status</th><th>CPU usage</th><th>CPU temperature</th>"
            + ("<th>GPU temperature</th>" if any_gpu else "") + "<th>Last report</th></tr>")
    note = (f"<p class='sub' style='margin-top:10px'>Values from each computer's last 10-minute report. "
            f"Marks on temperature bars: {TEMP_WARN} °C and {TEMP_CRIT} °C. Gray = offline (last known value).</p>")
    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page("Lab monitor", f"<h1>Lab monitor</h1><p class='sub'>{n} computers · updated {stamp}</p>{nav('Latest')}"
                      f"<table>{head}{''.join(rows)}</table>{note}"))
    with open(os.path.join(a.out, "history.html"), "w", encoding="utf-8") as fh:
        fh.write(page("Lab monitor - history",
                      f"<h1>Lab monitor</h1><p class='sub'>{n} computers · last {CARD_HOURS} h · times in UTC · updated {stamp}"
                      f" · click a computer for {a.days} days</p>{nav('History')}<div class='grid'>{''.join(cards)}</div>"))
    print(f"built dashboard for {n} computers in {a.out}/")


if __name__ == "__main__":
    main()
