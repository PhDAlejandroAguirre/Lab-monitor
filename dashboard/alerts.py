#!/usr/bin/env python3
"""
Email alerts for hot computers. Runs right after the dashboard is built.

Rules (per computer, using its latest report):
  - CPU or GPU temperature >= ALERT_TEMP           -> "HOT" email (once)
  - still hot after ALERT_REPEAT_HOURS              -> reminder email
  - back below ALERT_TEMP - 5 °C (or stops reporting while hot) -> "back to normal" email
All computers that changed in one run go into a single email.

Settings come from environment variables (set as GitHub repository secrets/variables):
  SMTP_USER, SMTP_PASSWORD   (secrets)  the sending account, e.g. a Gmail address + app password
  ALERT_TO                   (variable) recipients, comma-separated (default: SMTP_USER)
  ALERT_TEMP                 (variable) threshold in °C (default 90)
  ALERT_REPEAT_HOURS         (variable) reminder interval while still hot (default 6)
  SMTP_HOST / SMTP_PORT      (variable) default smtp.gmail.com / 465
If SMTP_USER or SMTP_PASSWORD is missing, the script does nothing.

State (who is hot, when we last emailed) is kept in a small JSON file between runs.
"""

import argparse
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_plots  # noqa: E402  (same folder: reuses data loading, names and Chile time)

FRESH = timedelta(minutes=30)   # only judge computers whose last report is this recent
HYSTERESIS = 5                  # °C below the threshold before "back to normal"
LABS = {}                       # computer name -> lab, filled by current_temps()


def who(name):
    lab = LABS.get(name)
    return f"{name} ({lab})" if lab and lab != make_plots.UNASSIGNED else name


def env(name, default=""):
    v = os.environ.get(name, "")
    return v.strip() if v.strip() else default


def current_temps(data_dir, names_file, now):
    """{name: (max temp of CPU/GPU, which, last report time)} for computers with a fresh report."""
    df = make_plots.load(data_dir, names_file)
    out = {}
    if df.empty:
        return out
    LABS.update(df.groupby("name")["lab_name"].last().to_dict())
    for name, d in df.groupby("name"):
        last = d.iloc[-1]
        seen = last["timestamp_utc"].to_pydatetime()
        if now - seen > FRESH:
            continue
        cpu, gpu = last["cpu_temp_max"], last["gpu_temp_max"]
        cands = [(t, w) for t, w in ((cpu, "CPU"), (gpu, "GPU")) if t == t]  # drop NaN
        if cands:
            t, which = max(cands)
            out[name] = (float(t), which, seen)
    return out


def decide(state, temps, now, threshold, repeat):
    """Update state in place; return (hot_new, hot_still, cooled) lists for the email."""
    hot_new, hot_still, cooled = [], [], []
    for name, (t, which, seen) in temps.items():
        s = state.get(name, {"hot": False})
        if t >= threshold:
            last = datetime.fromisoformat(s["last_sent"]) if s.get("last_sent") else None
            if not s.get("hot"):
                hot_new.append((name, t, which, seen))
                state[name] = {"hot": True, "last_sent": now.isoformat(), "since": now.isoformat()}
            elif last is None or now - last >= repeat:
                hot_still.append((name, t, which, seen, s.get("since")))
                s["last_sent"] = now.isoformat()
        elif s.get("hot") and t < threshold - HYSTERESIS:
            cooled.append((name, t, which, seen))
            state[name] = {"hot": False}
    # a hot computer that stopped reporting: tell once, then forget it
    for name, s in list(state.items()):
        if s.get("hot") and name not in temps:
            cooled.append((name, None, None, None))
            state[name] = {"hot": False}
    return hot_new, hot_still, cooled


def compose(hot_new, hot_still, cooled, threshold, url):
    fmt = make_plots.fmt_dt
    lines, subject_bits = [], []
    if hot_new:
        subject_bits.append("HOT: " + ", ".join(who(n) for n, *_ in hot_new))
        lines.append(f"Over {threshold:.0f} °C now:")
        lines += [f"  • {who(n)}: {which} {t:.0f} °C  (report {fmt(seen)})" for n, t, which, seen in hot_new]
        lines.append("")
    if hot_still:
        subject_bits.append("still hot: " + ", ".join(who(n) for n, *_ in hot_still))
        lines.append(f"Still over {threshold:.0f} °C:")
        for n, t, which, seen, since in hot_still:
            extra = f", hot since {fmt(datetime.fromisoformat(since))}" if since else ""
            lines.append(f"  • {who(n)}: {which} {t:.0f} °C  (report {fmt(seen)}{extra})")
        lines.append("")
    if cooled:
        subject_bits.append("back to normal: " + ", ".join(who(n) for n, *_ in cooled))
        lines.append("Back to normal:")
        for n, t, which, seen in cooled:
            lines.append(f"  • {who(n)}: {which} {t:.0f} °C  (report {fmt(seen)})" if t is not None
                         else f"  • {who(n)}: stopped reporting (off or offline) while it was hot")
        lines.append("")
    lines.append(f"All times in {make_plots.TZ_LABEL}.")
    if url:
        lines.append(f"Dashboard: {url}")
    icon = "🔥" if (hot_new or hot_still) else "✅"
    return f"{icon} Lab monitor – " + "; ".join(subject_bits), "\n".join(lines)


def send(subject, body):
    user, password = env("SMTP_USER"), env("SMTP_PASSWORD")
    to = [a.strip() for a in env("ALERT_TO", user).split(",") if a.strip()]
    host, port = env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "465"))
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, ", ".join(to)
    msg.set_content(body)
    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(user, password)
            s.send_message(msg)
    print(f"email sent to {', '.join(to)}: {subject}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--names", default="computers.json")
    ap.add_argument("--state", default="alert-state/state.json")
    ap.add_argument("--test-email", action="store_true", help="send a test email and exit")
    a = ap.parse_args()

    if not (env("SMTP_USER") and env("SMTP_PASSWORD")):
        print("alerts: SMTP_USER / SMTP_PASSWORD not set - skipping (see README, 'Email alerts')")
        return

    repo = env("GITHUB_REPOSITORY")
    url = f"https://{repo.split('/')[0].lower()}.github.io/{repo.split('/')[1]}/" if "/" in repo else ""

    if a.test_email:
        send("✅ Lab monitor – test email", "Email alerts are set up correctly.\n" + (f"Dashboard: {url}" if url else ""))
        return

    threshold = float(env("ALERT_TEMP", "90"))
    repeat = timedelta(hours=float(env("ALERT_REPEAT_HOURS", "6")))
    now = datetime.now(timezone.utc)

    state = {}
    if os.path.exists(a.state):
        try:
            with open(a.state, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    temps = current_temps(a.data, a.names, now)
    hot_new, hot_still, cooled = decide(state, temps, now, threshold, repeat)
    print(f"alerts: {len(temps)} computers checked, threshold {threshold:.0f} °C, "
          f"new hot {len(hot_new)}, still hot {len(hot_still)}, back to normal {len(cooled)}")

    if hot_new or hot_still or cooled:
        subject, body = compose(hot_new, hot_still, cooled, threshold, url)
        send(subject, body)        # if sending fails, the state is not saved, so it retries next run

    os.makedirs(os.path.dirname(a.state) or ".", exist_ok=True)
    with open(a.state, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


if __name__ == "__main__":
    main()
