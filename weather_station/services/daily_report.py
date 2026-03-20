# services/daily_report.py
import os
import subprocess
import logging
from datetime import datetime, timezone

logger = logging.getLogger("DailyReport")

ALERT_AUDIO_ROOT = "/home/ufuser/Fpren-main/audio_playlist/alerts"
RECIPIENT        = "lbornacelli@gmail.com"
TIMESTAMP_FILE   = "/home/ufuser/Fpren-main/weather_station/logs/last_daily_report.txt"


def _load_last_run() -> datetime:
    """Return the timestamp of the last report, or epoch if never run."""
    try:
        if os.path.exists(TIMESTAMP_FILE):
            with open(TIMESTAMP_FILE) as f:
                ts = float(f.read().strip())
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _save_last_run(dt: datetime):
    os.makedirs(os.path.dirname(TIMESTAMP_FILE), exist_ok=True)
    with open(TIMESTAMP_FILE, "w") as f:
        f.write(str(dt.timestamp()))


def _collect_new_wavs(since: datetime) -> list[dict]:
    """Walk alert folders and return WAVs created after `since`."""
    new_files = []
    for root, _, files in os.walk(ALERT_AUDIO_ROOT):
        for fname in sorted(files):
            if not fname.lower().endswith(".wav"):
                continue
            fpath = os.path.join(root, fname)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            except OSError:
                continue
            if mtime > since:
                new_files.append({
                    "path":       fpath,
                    "filename":   fname,
                    "event_type": os.path.basename(root),
                    "created_at": mtime.strftime("%Y-%m-%d %H:%M:%S UTC"),
                })
    new_files.sort(key=lambda x: x["created_at"])
    return new_files


def _build_email_body(new_wavs: list[dict], since: datetime) -> str:
    now_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    since_str = since.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "Beacon Weather Station — Daily Alert Report",
        f"Period : {since_str} → {now_str}",
        f"New alerts converted to WAV: {len(new_wavs)}",
        "",
    ]

    if not new_wavs:
        lines.append("No new alerts were issued during this period.")
    else:
        # Group by event type
        by_type: dict[str, list] = {}
        for w in new_wavs:
            by_type.setdefault(w["event_type"], []).append(w)

        for event_type, wavs in sorted(by_type.items()):
            lines.append(f"[ {event_type.upper().replace('_', ' ')} ] — {len(wavs)} alert(s)")
            for w in wavs:
                lines.append(f"  {w['created_at']}  {w['filename']}")
            lines.append("")

    lines += [
        "---",
        "Beacon Weather Station | Gainesville, FL",
        f"Log: /home/ufuser/Fpren-main/weather_station/logs/alert_service.log",
    ]
    return "\n".join(lines)


def send_daily_report():
    """Send a daily email summary of new alert WAVs via local Postfix relay."""
    since    = _load_last_run()
    now      = datetime.now(timezone.utc)
    new_wavs = _collect_new_wavs(since)

    if not new_wavs:
        logger.info("Daily report: no new alerts since last run — skipping email.")
        _save_last_run(now)
        return

    subject = f"Beacon Daily Alert Report — {len(new_wavs)} new alert(s)"
    body    = _build_email_body(new_wavs, since)

    raw_email = (
        f"To: {RECIPIENT}\n"
        f"From: {RECIPIENT}\n"
        f"Subject: {subject}\n"
        f"Content-Type: text/plain\n"
        f"\n"
        f"{body}"
    )

    try:
        proc = subprocess.run(
            ["sendmail", "-t"],
            input=raw_email,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            logger.info(f"Daily report sent: {len(new_wavs)} new alert(s).")
        else:
            logger.error(f"sendmail failed: {proc.stderr}")
    except Exception as e:
        logger.error(f"Daily report email error: {e}")

    _save_last_run(now)
