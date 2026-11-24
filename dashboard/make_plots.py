#!/usr/bin/env python3
"""
Build the static dashboard from data/<machine_id>.csv (one file per computer).

Output in --out (default "site/"):
    index.html     LATEST view: per lab, each computer's last report + status, and a temperature strip
    history.html   HISTORY view: per lab, a grid of small charts (last 48 h)
    pc-*.html      detail page per computer (full retention period)
    img/*.png      the charts

Name and lab of each computer, in order of priority:
  1. computers.json (repo root), keyed by machine id or hostname. A value can be a name
     ("Lab1-PC03") or a name and lab ({"name": "Lab1-PC03", "lab": "Lab1"}).
  2. the name and lab typed in the installer (display_name / lab columns of the reports);
  3. the hostname, in the group "Unassigned".
Computers with the same name are shown as one (e.g. both OSes of a dual-boot PC).
"""

import argparse
import glob
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.ticker
import matplotlib.pyplot as plt
import pandas as pd

# ---- settings you may want to change
LAB_NAME = "Computational Heat and Fluid Flow Lab"   # shown in the page header
CARD_HOURS = 48                      # time window of the small charts in the History view
STALE_AFTER = timedelta(minutes=45)  # "Offline" if no report for this long
TEMP_WARN, TEMP_CRIT = 80, 90        # degC (CPU or GPU)
TZ = ZoneInfo("America/Santiago")    # time zone used everywhere on the dashboard (handles daylight saving)
TZ_LABEL = "Chile time"
UNASSIGNED = "Unassigned"

# ---- dark palette (charts use the same values as the page)
SURFACE, GRID, AXIS = "#16202a", "#26333f", "#3a4856"
INK, INK2, MUTED = "#e6edf3", "#9fb0c0", "#6f8192"
BLUE, ORANGE, TEAL = "#5aa9f0", "#f28c4f", "#3fc1a5"
LAB_COLORS = ["#4fd1e0", "#b49cff", "#7bd88f", "#ffb86b", "#ff8fb1", "#e4d96f"]   # lab tags, in lab order

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
    "axes.spines.top": False, "axes.spines.right": False,
    "xtick.color": INK2, "ytick.color": INK2, "font.size": 8,
    "font.family": "sans-serif", "font.sans-serif": ["IBM Plex Sans", "DejaVu Sans"],
    "legend.frameon": False, "legend.labelcolor": INK2, "lines.linewidth": 1.5,
})

NUM = ["cpu_avg", "cpu_max", "core_max", "cpu_temp_avg", "cpu_temp_max", "ram_pct", "gpu_util_avg", "gpu_temp_max"]


def natural(s):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(s))]


# --------------------------------------------------------------------------- data

def load(data_dir, names_file):
    names = {}
    if names_file and os.path.exists(names_file):
        with open(names_file, encoding="utf-8-sig") as f:
            for k, v in json.load(f).items():
                if k.startswith("_"):
                    continue
                names[k.lower()] = v if isinstance(v, dict) else {"name": v}
    frames = []
    for f in sorted(glob.glob(os.path.join(data_dir, "*.csv"))):
        try:
            d = pd.read_csv(f, dtype={"machine_id": str, "hostname": str, "os": str, "display_name": str, "lab": str})
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
    df["t_local"] = df["timestamp_utc"].dt.tz_convert(TZ).dt.tz_localize(None)   # for charts
    for c in NUM:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    for c in ("display_name", "lab", "hostname"):
        if c not in df:
            df[c] = ""
        df[c] = df[c].fillna("").astype(str).str.strip()

    latest = df.groupby("file_id").last()
    display, labs = {}, {}
    for fid, row in latest.iterrows():
        host = row["hostname"] or "unknown"
        m = names.get(fid.lower()) or names.get(host.lower()) or {}
        display[fid] = m.get("name") or row["display_name"] or None
        labs[fid] = m.get("lab") or row["lab"] or UNASSIGNED
    unnamed = {fid: (latest.loc[fid, "hostname"] or "unknown") for fid, v in display.items() if not v}
    counts = pd.Series(list(unnamed.values())).value_counts() if unnamed else {}
    for fid, host in unnamed.items():   # two different PCs both called "ubuntu"? keep them apart
        display[fid] = host if counts[host] == 1 else f"{host} ({fid[-4:]})"
    df["name"] = df["file_id"].map(display)
    df["lab_name"] = df["file_id"].map(labs)
    return df


def page_id(name):
    return "pc-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "x")


def resample(d, freq):
    """Bin the reports (mean for averages, max for maxima). Empty bins become NaN,
    so lines break where the computer was off instead of bridging the gap."""
    agg = {c: ("max" if c.endswith("max") else "mean") for c in
           ["cpu_avg", "core_max", "cpu_temp_max", "ram_pct", "gpu_util_avg", "gpu_temp_max"]}
    return d.set_index("t_local")[list(agg)].resample(freq).agg(agg)


def state_of(last_seen, temp, now):
    if now - last_seen > STALE_AFTER:
        return "off"
    if pd.notna(temp) and temp >= TEMP_CRIT:
        return "hot"
    if pd.notna(temp) and temp >= TEMP_WARN:
        return "warm"
    return "ok"


LABEL = {"ok": "Normal", "warm": "Warm", "hot": "Hot", "off": "Offline"}


def heat(t):
    return "hot" if pd.notna(t) and t >= TEMP_CRIT else ("warm" if pd.notna(t) and t >= TEMP_WARN else "")


# --------------------------------------------------------------------------- time

def local_naive(ts):
    """A UTC datetime as Chile local time without tzinfo (what the charts use)."""
    return ts.astimezone(TZ).replace(tzinfo=None)


def fmt_dt(ts):
    """DD/MM/YYYY HH:MM in Chile time."""
    return ts.astimezone(TZ).strftime("%d/%m/%Y %H:%M")


# --------------------------------------------------------------------------- charts

def time_axis(ax, start, end, compact=False):
    """Fixed time window [start, end] with readable ticks, even where there is no data."""
    hours = (end - start).total_seconds() / 3600
    ax.set_xlim(start, end)
    if hours <= 72:
        step = 12 if compact else 6
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, step)))
        ax.xaxis.set_minor_locator(mdates.HourLocator(byhour=range(0, 24, 3 if compact else 1)))
        # date on the midnight tick, time on the others
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(
            lambda x, _: (lambda t: t.strftime("%d/%m") if t.hour == 0 else t.strftime("%H:%M"))(mdates.num2date(x))))
    else:
        ax.xaxis.set_major_locator(mdates.DayLocator(interval=2 if hours > 24 * 10 else 1))
        ax.xaxis.set_minor_locator(mdates.DayLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    ax.tick_params(axis="x", which="minor", length=2, color=AXIS)


def _panels(axes, r, compact):
    """Draw the three panels (CPU %, temperature, memory + GPU load) on axes."""
    has_gpu = r["gpu_temp_max"].notna().any()
    fs = 7 if compact else 8
    legend = (dict(loc="lower right", bbox_to_anchor=(1, 0.98), ncol=2, fontsize=7, handlelength=1.2,
                   borderaxespad=0, columnspacing=0.8) if compact else
              dict(loc="lower right", bbox_to_anchor=(1, 1), ncol=2))
    title = (lambda ax, t: ax.set_title(t, fontsize=8, color=INK2, fontweight="normal")) if compact else \
            (lambda ax, t: ax.set_title(t))

    ax = axes[0]
    ax.plot(r["cpu_avg"], color=BLUE, label="CPU average")
    if not compact:
        ax.plot(r["core_max"], color=MUTED, linewidth=0.9, label="Busiest core")
        ax.legend(**legend)
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 50, 100])
    title(ax, "CPU usage %")

    ax = axes[1]
    ax.plot(r["cpu_temp_max"], color=BLUE, label="CPU")
    if has_gpu:
        ax.plot(r["gpu_temp_max"], color=ORANGE, label="GPU")
    ax.axhline(TEMP_CRIT, color=MUTED, linewidth=0.7, linestyle="--")
    if not compact:
        ax.text(1.0, TEMP_CRIT, f" {TEMP_CRIT} °C", transform=ax.get_yaxis_transform(), color=INK2, va="center", fontsize=8)
    ax.set_ylim(20, 100)
    ax.set_yticks([20, 40, 60, 80, 100] if not compact else [20, 60, 100])
    title(ax, "Temperature °C (max)")
    if has_gpu or not compact:
        ax.legend(**legend)

    ax = axes[2]
    ax.plot(r["ram_pct"], color=TEAL, label="RAM used")
    if has_gpu:
        ax.plot(r["gpu_util_avg"], color=ORANGE, label="GPU load")
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 50, 100])
    title(ax, "Memory and GPU load %")
    ax.legend(**legend)
    for a in axes:
        a.tick_params(labelsize=fs)


def small_card(d, now, path):
    """Compact chart for the History grid: the three panels over the last CARD_HOURS."""
    end = local_naive(now)
    start = end - timedelta(hours=CARD_HOURS)
    r = resample(d[d["t_local"] >= start], "10min")
    fig, axes = plt.subplots(3, 1, figsize=(3.9, 3.5), sharex=True, gridspec_kw={"hspace": 0.42})
    _panels(axes, r, compact=True)
    time_axis(axes[-1], start, end, compact=True)
    fig.subplots_adjust(left=0.1, right=0.98, top=0.93, bottom=0.1)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def detail_figure(d, now, hours, path):
    """Full-size chart for a computer's page over a fixed window of `hours`."""
    end = local_naive(now)
    start = end - timedelta(hours=hours)
    r = resample(d[d["t_local"] >= start], "10min" if hours <= 72 else "1h")
    fig, axes = plt.subplots(3, 1, figsize=(10, 6.6), sharex=True)
    _panels(axes, r, compact=False)
    time_axis(axes[-1], start, end)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------------- HTML

FONTS = ("<link rel='preconnect' href='https://fonts.googleapis.com'>"
         "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
         "<link href='https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap' rel='stylesheet'>")

CSS = """
:root{color-scheme:dark;--page:#0e151c;--bar:#090e13;--surface:#16202a;--raise:#1b2733;--ink:#e6edf3;--ink2:#9fb0c0;--muted:#6f8192;
--line:#26333f;--soft:#223040;--accent:#5aa9f0;--warm:#f2b33d;--hot:#f0605d;--ok:#45b97c;--off:#5f7080}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 'IBM Plex Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
font-variant-numeric:tabular-nums}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
a:focus-visible,th:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}
.top{background:var(--bar);border-bottom:1px solid var(--line)}
.top .in{max-width:1280px;margin:0 auto;padding:16px 28px 0;display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:8px 24px}
.brand .lab{font-size:13px;color:var(--ink2)}
.brand h1{margin:2px 0 0;font-size:22px;font-weight:600;letter-spacing:-.01em}
.updated{font-size:13px;color:var(--ink2);padding-bottom:4px} .updated b{color:var(--ink);font-weight:500}
nav{width:100%;display:flex;gap:2px;margin-top:10px}
nav a{color:var(--ink2);padding:9px 16px;font-weight:500;border-radius:6px 6px 0 0}
nav a:hover{color:var(--ink);text-decoration:none;background:var(--raise)}
nav a.on{background:var(--page);color:var(--ink);box-shadow:inset 0 2px 0 var(--accent)}
main{max-width:1280px;margin:0 auto;padding:22px 28px 40px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px}
footer{max-width:1280px;margin:0 auto;padding:0 28px 32px;color:var(--muted);font-size:12.5px}

/* lab tag: the lab's own color, shown beside every computer name */
.tag{display:inline-block;padding:1px 8px;border-radius:5px;font-size:12px;font-weight:600;line-height:18px;
color:var(--lc);background:color-mix(in srgb,var(--lc) 16%,transparent);border:1px solid color-mix(in srgb,var(--lc) 40%,transparent);
vertical-align:1px;white-space:nowrap}
.tag.big{font-size:14px;padding:2px 10px;line-height:22px}

/* temperature strip */
.thermal{padding:18px 22px 16px;margin-bottom:22px;display:grid;grid-template-columns:minmax(0,1fr) 210px;gap:28px;align-items:center}
.thermal h2{margin:0 0 2px;font-size:15px;font-weight:600}
.thermal .hint{margin:0 0 8px;color:var(--ink2);font-size:13px}
.srow{display:grid;grid-template-columns:104px minmax(0,1fr);align-items:end;gap:12px}
.srow .tag{margin-bottom:12px;justify-self:start}
.scale{position:relative;margin:0 6px}
.track{position:absolute;left:0;right:0;bottom:18px;height:10px;border-radius:5px;
background:linear-gradient(90deg,#1d3b57 0%,#2c6496 50%,#8a6a1f var(--w),#8a6a1f var(--h),#8d2f2e var(--h),#c2413f 100%)}
.tick{position:absolute;bottom:0;transform:translateX(-50%);font-size:11px;color:var(--muted)}
.zone{position:absolute;bottom:14px;width:1px;height:18px;background:var(--ink2);opacity:.35}
.dot{position:absolute;width:13px;height:13px;margin-left:-6.5px;border-radius:50%;background:var(--accent);border:2px solid var(--surface)}
.dot.warm{background:var(--warm)} .dot.hot{background:var(--hot)}
.dot.off{background:var(--surface);box-shadow:0 0 0 1.5px var(--off)}
.flag{position:absolute;transform:translateX(-50%);font-size:11.5px;font-weight:600;white-space:nowrap;color:var(--ink)}
.counts{display:grid;gap:8px;border-left:1px solid var(--line);padding-left:24px}
.count{display:flex;align-items:baseline;gap:10px;font-size:13px;color:var(--ink2)}
.count b{font-size:22px;font-weight:600;color:var(--ink);min-width:34px;text-align:right}
.count.hot b{color:var(--hot)} .count.warm b{color:var(--warm)}

/* lab sections */
.labhead{display:flex;align-items:center;gap:12px;margin:26px 0 10px}
.labhead:first-of-type{margin-top:0}
.labhead h2{margin:0;font-size:17px;font-weight:600}
.labhead .sum{color:var(--ink2);font-size:13px}
.labhead .sum b{font-weight:600} .labhead .sum .hot{color:var(--hot)} .labhead .sum .warm{color:var(--warm)}

/* table */
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;min-width:940px;table-layout:fixed}
col.c-name{width:20%} col.c-status{width:9%} col.c-when{width:13%}
th{font-size:12.5px;font-weight:500;color:var(--ink2);text-align:left;padding:12px 14px;border-bottom:1px solid var(--line);
white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--ink)} th .arr{color:var(--muted);font-size:10px;margin-left:4px}
td{padding:11px 14px;border-bottom:1px solid #1f2b36;white-space:nowrap;vertical-align:middle}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--raise)}
td.name{border-left:3px solid transparent}
tr.s-warm td.name{border-left-color:var(--warm)} tr.s-hot td.name{border-left-color:var(--hot)} tr.s-off td.name{border-left-color:var(--off)}
.name a{font-weight:600;color:var(--ink);margin-right:8px} .name small{display:block;color:var(--muted);font-size:12px}
.status{display:inline-flex;align-items:center;gap:6px;font-size:13px;font-weight:500}
.status i{width:8px;height:8px;border-radius:50%;background:var(--ok)}
.status.warm i{background:var(--warm)} .status.hot i{background:var(--hot)} .status.off i{background:transparent;box-shadow:0 0 0 1.5px var(--off)}
.status.off{color:var(--ink2)}
.bw{display:flex;align-items:center;gap:10px}
.bar{flex:1;height:8px;background:var(--soft);border-radius:4px;position:relative}
.bar span{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--accent)}
.bar span.warm{background:var(--warm)} .bar span.hot{background:var(--hot)}
.bar i{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--muted)}
tr.off .bar span{background:#3b4a58}
.v{width:48px;text-align:right;font-weight:500} tr.off .v,.na{color:var(--muted)}
.when{color:var(--ink2);font-size:13px}

/* history cards */
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.card{padding:14px 14px 8px}
.card .hd{display:flex;justify-content:space-between;align-items:center;gap:8px}
.card .hd .nm{display:flex;align-items:center;gap:8px;min-width:0}
.card .hd a{font-weight:600;color:var(--ink);font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card .sub{color:var(--muted);font-size:12.5px;margin:2px 0 8px}
.stats{display:flex;gap:18px;margin-bottom:6px}
.stats div{font-size:12px;color:var(--ink2)} .stats b{display:block;font-size:16px;font-weight:600;color:var(--ink)}
.stats b.warm{color:var(--warm)} .stats b.hot{color:var(--hot)}
.card img{width:100%;height:auto;display:block}
.card.off .stats b{color:var(--muted)}

/* detail */
.back{display:inline-block;margin-bottom:12px;font-size:13px}
.title{display:flex;align-items:center;gap:12px;margin:0 0 14px} .title h2{margin:0;font-size:24px;font-weight:600}
.facts{display:flex;flex-wrap:wrap;gap:10px 36px;padding:16px 20px;margin-bottom:16px}
.facts div{font-size:12.5px;color:var(--ink2)} .facts b{display:block;font-size:15px;font-weight:500;color:var(--ink)}
.chart{padding:12px} .chart img{width:100%;height:auto;display:block}
.sec{margin:22px 0 10px;font-size:15px;font-weight:600}
.empty{padding:40px;text-align:center;color:var(--ink2)} .empty b{display:block;color:var(--ink);font-size:16px;margin-bottom:6px}

@media (max-width:760px){
 .top .in,main,footer{padding-left:16px;padding-right:16px}
 .thermal{grid-template-columns:1fr} .counts{border-left:0;padding-left:0;grid-template-columns:repeat(2,1fr)}
 .srow{grid-template-columns:1fr} .srow .tag{margin-bottom:0}
}
"""

SORT_JS = """
<script>
document.querySelectorAll('table.sortable').forEach(function(t){
  var ths=t.querySelectorAll('th'), body=t.tBodies[0];
  ths.forEach(function(th,i){
    th.tabIndex=0;
    function go(){
      var asc=th.dataset.dir!=='asc'; ths.forEach(function(h){h.dataset.dir='';var a=h.querySelector('.arr');if(a)a.textContent='';});
      th.dataset.dir=asc?'asc':'desc'; th.querySelector('.arr').textContent=asc?'▲':'▼';
      var rows=[].slice.call(body.rows);
      rows.sort(function(a,b){
        var x=a.cells[i].dataset.v, y=b.cells[i].dataset.v, nx=parseFloat(x), ny=parseFloat(y);
        var c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:String(x).localeCompare(String(y),undefined,{numeric:true});
        return asc?c:-c;});
      rows.forEach(function(r){body.appendChild(r);});
    }
    th.addEventListener('click',go); th.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}});
  });
});
</script>
"""


def page(title, active, updated, content, script=""):
    tabs = [("index.html", "Latest"), ("history.html", "History")]
    navhtml = "".join(f"<a href='{h}' class='{'on' if t == active else ''}'>{t}</a>" for h, t in tabs)
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<meta name='theme-color' content='#090e13'>"
            f"<meta http-equiv='refresh' content='600'><title>{html.escape(title)}</title>{FONTS}"
            f"<style>{CSS}</style></head><body>"
            f"<header class='top'><div class='in'><div class='brand'><div class='lab'>{html.escape(LAB_NAME)}</div>"
            f"<h1>Computer monitor</h1></div><div class='updated'>Updated <b>{updated}</b></div>"
            f"<nav>{navhtml}</nav></div></header>"
            f"<main>{content}</main>"
            f"<footer>Each computer reports every 10 minutes; this page refreshes itself. "
            f"All times are {TZ_LABEL} ({TZ.key}). Warm from {TEMP_WARN} °C, hot from {TEMP_CRIT} °C.</footer>"
            f"{script}</body></html>")


def tag(lab, color, big=False):
    return f"<span class='tag{' big' if big else ''}' style='--lc:{color}'>{html.escape(lab)}</span>"


def bar(value, vmax, unit, cls="", ticks=()):
    if pd.isna(value):
        return "<div class='bw'><div class='bar'></div><span class='v na'>–</span></div>"
    w = max(0.0, min(100.0, value / vmax * 100))
    t = "".join(f"<i style='left:{x / vmax * 100:.1f}%'></i>" for x in ticks)
    return (f"<div class='bw'><div class='bar'><span class='{cls}' style='width:{w:.1f}%'></span>{t}</div>"
            f"<span class='v'>{value:.0f}{unit}</span></div>")


def num(v):
    return "" if pd.isna(v) else f"{v:.1f}"


SCALE_MIN, SCALE_MAX = 20, 100


def thermal_scale(items, show_ticks):
    """items: list of (name, temp or NaN, state). One dot per computer on a 20-100 °C scale.
    Warm/hot computers get a name label; labels that would overlap are lifted to another row."""
    pos = lambda t: (min(max(t, SCALE_MIN), SCALE_MAX) - SCALE_MIN) / (SCALE_MAX - SCALE_MIN) * 100
    stacks, dots, flagged = {}, [], []
    for name, t, state in sorted(items, key=lambda x: (x[1] if pd.notna(x[1]) else -1)):
        if pd.isna(t):
            continue
        key = round(pos(t) / 1.6)                        # dots closer than ~1.6 % share a column
        level = stacks.get(key, 0)
        stacks[key] = level + 1
        cls = "off" if state == "off" else heat(t)
        tip = f"{name}: {t:.0f} °C" + (" (offline, last known)" if state == "off" else "")
        dots.append((pos(t), level, cls, tip))
        if cls in ("warm", "hot"):
            flagged.append((pos(t), name))
    top_dot = 16 + 11 * (max(stacks.values(), default=1) - 1) + 14
    flags, rows_end = [], []
    for x, name in flagged:
        width = 0.75 * len(name) + 1.5                   # rough label width in % of the strip
        r = 0
        while r < len(rows_end) and x - width / 2 < rows_end[r]:
            r += 1
        if r == len(rows_end):
            rows_end.append(0)
        rows_end[r] = x + width / 2
        flags.append(f"<span class='flag' style='left:{x:.2f}%;bottom:{top_dot + 4 + r * 16}px'>{html.escape(name)}</span>")
    height = top_dot + 6 + 16 * len(rows_end)
    w, h = pos(TEMP_WARN), pos(TEMP_CRIT)
    ticks = "".join(f"<span class='tick' style='left:{pos(x):.2f}%'>{x}°</span>"
                    for x in range(SCALE_MIN, SCALE_MAX + 1, 10)) if show_ticks else ""
    zones = f"<span class='zone' style='left:{w:.2f}%'></span><span class='zone' style='left:{h:.2f}%'></span>"
    dothtml = "".join(f"<span class='dot {c}' style='left:{x:.2f}%;bottom:{16 + lv * 11}px' title='{html.escape(tip)}'></span>"
                      for x, lv, c, tip in dots)
    return (f"<div class='scale' style='--w:{w:.2f}%;--h:{h:.2f}%;height:{height}px'>"
            f"<div class='track'></div>{zones}{ticks}{dothtml}{''.join(flags)}</div>")


def lab_summary(states):
    n = len(states)
    hot, warm, off = states.count("hot"), states.count("warm"), states.count("off")
    parts = [f"{n} computer{'s' if n != 1 else ''}"]
    if hot:
        parts.append(f"<b class='hot'>{hot} hot</b>")
    if warm:
        parts.append(f"<b class='warm'>{warm} warm</b>")
    if off:
        parts.append(f"{off} offline")
    return ", ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--names", default="computers.json")
    ap.add_argument("--out", default="site")
    ap.add_argument("--days", type=int, default=20, help="history shown on detail pages")
    a = ap.parse_args()

    os.makedirs(os.path.join(a.out, "img"), exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = fmt_dt(now)
    df = load(a.data, a.names)
    if not df.empty:
        df = df[df["timestamp_utc"] >= now - timedelta(days=a.days)]

    if df.empty:
        empty = ("<div class='panel empty'><b>No computer has reported yet</b>"
                 "Install the agent on a lab computer (README, section 3). Its first report appears here within 10 minutes.</div>")
        for f, t in (("index.html", "Latest"), ("history.html", "History")):
            with open(os.path.join(a.out, f), "w", encoding="utf-8") as fh:
                fh.write(page("Computer monitor", t, stamp, empty))
        print("no data")
        return

    any_gpu = df["gpu_temp_max"].notna().any()
    # one entry per computer, grouped by lab
    computers = []
    for name, d in df.groupby("name"):
        last = d.iloc[-1]
        temps = [t for t in (last["cpu_temp_max"], last["gpu_temp_max"]) if pd.notna(t)]
        hottest = max(temps) if temps else float("nan")
        computers.append(dict(name=name, lab=last["lab_name"], d=d, last=last, hottest=hottest,
                              state=state_of(last["timestamp_utc"], hottest, now)))
    labs = sorted({c["lab"] for c in computers}, key=lambda s: (s == UNASSIGNED, natural(s)))
    color = {lab: ("#8a96a3" if lab == UNASSIGNED else LAB_COLORS[i % len(LAB_COLORS)]) for i, lab in enumerate(labs)}

    arr = "<span class='arr'></span>"
    cols = ("<colgroup><col class='c-name'><col class='c-status'><col><col>" + ("<col>" if any_gpu else "")
            + "<col><col class='c-when'></colgroup>")
    head = (f"<thead><tr><th>Computer{arr}</th><th>Status{arr}</th><th>CPU usage{arr}</th><th>CPU temperature{arr}</th>"
            + (f"<th>GPU temperature{arr}</th>" if any_gpu else "") + f"<th>RAM{arr}</th><th>Last report{arr}</th></tr></thead>")

    latest_html, history_html, strips = [], [], []
    for li, lab in enumerate(labs):
        group = sorted((c for c in computers if c["lab"] == lab), key=lambda c: natural(c["name"]))
        lc = color[lab]
        rows, cards = [], []
        for c in group:
            name, d, last, state = c["name"], c["d"], c["last"], c["state"]
            pid, h = page_id(name), html.escape(name)
            off = state == "off"
            osname = html.escape(str(last["os"]).capitalize())
            when = fmt_dt(last["timestamp_utc"].to_pydatetime())
            rank = {"hot": 0, "warm": 1, "ok": 2, "off": 3}[state]
            cpu_t, gpu_t = last["cpu_temp_max"], last["gpu_temp_max"]
            badge = f"<span class='status {state}'><i></i>{LABEL[state]}</span>"

            rows.append(
                f"<tr class='s-{state}{' off' if off else ''}'>"
                f"<td class='name' data-v='{h}'><a href='{pid}.html'>{h}</a>{tag(lab, lc)}<small>{osname}</small></td>"
                f"<td data-v='{rank}'>{badge}</td>"
                f"<td data-v='{num(last['cpu_avg'])}'>{bar(last['cpu_avg'], 100, '%')}</td>"
                f"<td data-v='{num(cpu_t)}'>{bar(cpu_t, 100, ' °C', '' if off else heat(cpu_t), (TEMP_WARN, TEMP_CRIT))}</td>"
                + (f"<td data-v='{num(gpu_t)}'>{bar(gpu_t, 100, ' °C', '' if off else heat(gpu_t), (TEMP_WARN, TEMP_CRIT))}</td>" if any_gpu else "")
                + f"<td data-v='{num(last['ram_pct'])}'>{bar(last['ram_pct'], 100, '%')}</td>"
                f"<td class='when' data-v='{last['timestamp_utc'].value}'>{when}</td></tr>")

            small_card(d, now, os.path.join(a.out, "img", f"{pid}_card.png"))
            stat = lambda lbl, v, unit, k="": f"<div>{lbl}<b class='{k}'>{'–' if pd.isna(v) else f'{v:.0f}{unit}'}</b></div>"
            cards.append(
                f"<div class='panel card{' off' if off else ''}'><div class='hd'><div class='nm'><a href='{pid}.html'>{h}</a>{tag(lab, lc)}</div>"
                f"{badge}</div><div class='sub'>{osname}, last report {when}</div>"
                f"<div class='stats'>{stat('CPU', last['cpu_avg'], '%')}{stat('CPU temp', cpu_t, ' °C', '' if off else heat(cpu_t))}"
                + (stat('GPU temp', gpu_t, ' °C', '' if off else heat(gpu_t)) if pd.notna(gpu_t) else "")
                + f"{stat('RAM', last['ram_pct'], '%')}</div>"
                f"<a href='{pid}.html' aria-label='Open {h}'><img src='img/{pid}_card.png' "
                f"alt='CPU usage, temperature and memory for {h}, last {CARD_HOURS} hours' loading='lazy'></a></div>")

            detail_figure(d, now, CARD_HOURS, os.path.join(a.out, "img", f"{pid}_48h.png"))
            detail_figure(d, now, a.days * 24, os.path.join(a.out, "img", f"{pid}_long.png"))
            ids = ", ".join(sorted(d["file_id"].unique()))
            facts = (f"<div class='panel facts'><div>Status<b>{badge}</b></div><div>Lab<b>{html.escape(lab)}</b></div>"
                     f"<div>Operating system<b>{osname}</b></div><div>Last report<b>{when}</b></div>"
                     f"<div>Hostname<b>{html.escape(str(last['hostname']))}</b></div><div>Machine id<b>{html.escape(ids)}</b></div></div>")
            with open(os.path.join(a.out, f"{pid}.html"), "w", encoding="utf-8") as fh:
                fh.write(page(f"{name} – Computer monitor", "", stamp,
                              f"<a class='back' href='index.html'>Back to all computers</a>"
                              f"<div class='title'><h2>{h}</h2>{tag(lab, lc, big=True)}</div>{facts}"
                              f"<h3 class='sec'>Last {CARD_HOURS} hours</h3>"
                              f"<div class='panel chart'><img src='img/{pid}_48h.png' alt='Usage and temperature for {h}, last {CARD_HOURS} hours'></div>"
                              f"<h3 class='sec'>Last {a.days} days</h3>"
                              f"<div class='panel chart'><img src='img/{pid}_long.png' alt='Usage and temperature for {h}, last {a.days} days' loading='lazy'></div>"))

        summary = lab_summary([c["state"] for c in group])
        section_head = f"<div class='labhead'>{tag(lab, lc, big=True)}<span class='sum'>{summary}</span></div>"
        latest_html.append(f"{section_head}<div class='panel tablewrap'><table class='sortable'>{cols}{head}"
                           f"<tbody>{''.join(rows)}</tbody></table></div>")
        history_html.append(f"{section_head}<div class='grid'>{''.join(cards)}</div>")
        strips.append(f"<div class='srow'>{tag(lab, lc)}"
                      f"{thermal_scale([(c['name'], c['hottest'], c['state']) for c in group], li == len(labs) - 1)}</div>")

    states = [c["state"] for c in computers]
    counts = (f"<div class='counts'><div class='count'><b>{len(states) - states.count('off')}</b>reporting now</div>"
              f"<div class='count hot'><b>{states.count('hot')}</b>hot (≥ {TEMP_CRIT} °C)</div>"
              f"<div class='count warm'><b>{states.count('warm')}</b>warm (≥ {TEMP_WARN} °C)</div>"
              f"<div class='count'><b>{states.count('off')}</b>offline</div></div>")
    hero = (f"<section class='panel thermal'><div><h2>Lab temperature</h2>"
            f"<p class='hint'>Each dot is a computer, placed at its hottest sensor (CPU or GPU). Hollow dots are offline. "
            f"Hover a dot for its name.</p>{''.join(strips)}</div>{counts}</section>")

    with open(os.path.join(a.out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page("Computer monitor", "Latest", stamp, hero + "".join(latest_html), SORT_JS))
    with open(os.path.join(a.out, "history.html"), "w", encoding="utf-8") as fh:
        fh.write(page("History – Computer monitor", "History", stamp,
                      f"<p style='margin:0 0 6px;color:var(--ink2)'>CPU usage, temperature and memory over the last {CARD_HOURS} hours. "
                      f"Open a computer for {a.days} days of detail.</p>" + "".join(history_html)))
    print(f"built dashboard for {len(computers)} computers in {len(labs)} lab(s) in {a.out}/")


if __name__ == "__main__":
    main()
