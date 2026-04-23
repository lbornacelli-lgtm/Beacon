"""
notifier.py — FPREN Alarm Notification Dispatcher

Sends:
  • Twilio SMS for Critical and Major alarms
  • SMTP email for configured severity levels

Reads Twilio credentials from environment (.env):
  TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, TWILIO_ALARM_NUMBERS

Reads SMTP settings from weather_rss/config/smtp_config.json (same file
the Flask dashboard uses, so no separate config needed).

Email control env vars (all in weather_station/.env):
  ALARM_EMAIL_ENABLED=true            — master switch (default false)
  ALARM_EMAIL_SEVERITIES=Critical,Major,Minor,Warning  — comma-separated list
  ALARM_EMAIL_CLEAR=true              — send recovery email on Critical/Major clear (default true)
"""
import json
import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from pymongo import MongoClient

log = logging.getLogger(__name__)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
SMTP_CFG_FILE = "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
DASHBOARD_URL = "http://128.227.67.234:5000/alarms"

TWILIO_ACCOUNT_SID    = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN     = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER    = os.environ.get("TWILIO_FROM_NUMBER", "")
TWILIO_ALARM_NUMBERS  = os.environ.get("TWILIO_ALARM_NUMBERS", "")

# Master email switch — set ALARM_EMAIL_ENABLED=true in .env to enable
ALARM_EMAIL_ENABLED = os.environ.get("ALARM_EMAIL_ENABLED", "false").lower() == "true"

# Which severities trigger email (comma-separated). Default: Critical and Major only.
# Set ALARM_EMAIL_SEVERITIES=Critical,Major,Minor,Warning to receive all levels.
_email_sev_raw = os.environ.get("ALARM_EMAIL_SEVERITIES", "Critical,Major")
EMAIL_SEVERITIES = {s.strip() for s in _email_sev_raw.split(",") if s.strip()}

# Send a recovery email when Critical/Major alarms clear (default: true)
ALARM_EMAIL_CLEAR = os.environ.get("ALARM_EMAIL_CLEAR", "true").lower() == "true"

SMS_SEVERITIES = {"Critical", "Major"}

_SEV_COLOR = {
    "Critical": "#e74c3c",
    "Major":    "#e67e22",
    "Minor":    "#f1c40f",
    "Warning":  "#3498db",
}
_SEV_LABEL = {
    "Critical": "CRITICAL ALARM",
    "Major":    "MAJOR ALARM",
    "Minor":    "MINOR ALARM",
    "Warning":  "WARNING",
}
_RESOLVED_COLOR = "#27ae60"


def _load_smtp() -> dict:
    try:
        with open(SMTP_CFG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


class Notifier:
    def __init__(self):
        try:
            db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=3000)["weather_rss"]
            self._notif_cfg = db["notification_config"].find_one({"_id": "singleton"}) or {}
        except Exception:
            self._notif_cfg = {}

    def send(self, severity: str, name: str, source: str, detail: str,
             remediation: str, subject_prefix: str = ""):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._send_email(severity, name, source, detail, remediation, ts, subject_prefix)
        if severity in SMS_SEVERITIES:
            self._send_sms(severity, name, source, detail, remediation, ts, subject_prefix)

    def send_clear(self, severity: str, name: str, source: str,
                   clear_detail: str = "", duration_str: str = ""):
        """Send a recovery/resolved notification for a cleared Critical or Major alarm."""
        if severity not in {"Critical", "Major"}:
            return
        if not ALARM_EMAIL_CLEAR:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        self._send_clear_email(severity, name, source, clear_detail, duration_str, ts)

    # ── Email ─────────────────────────────────────────────────────────────────

    def _get_recipients(self, smtp: dict) -> list:
        recipients = [e.strip() for e in smtp.get("mail_to", "").split(",") if e.strip()]
        extra = self._notif_cfg.get("notify_emails", "")
        if extra:
            recipients += [e.strip() for e in extra.split(",") if e.strip()]
        return list(dict.fromkeys(recipients))   # deduplicate, preserve order

    def _send_email(self, severity, name, source, detail, remediation, ts, prefix):
        if not ALARM_EMAIL_ENABLED:
            log.debug("Alarm email suppressed (ALARM_EMAIL_ENABLED=false): %s", name)
            return
        if severity not in EMAIL_SEVERITIES:
            log.debug("Alarm email skipped — severity %s not in ALARM_EMAIL_SEVERITIES: %s",
                      severity, name)
            return
        smtp = _load_smtp()
        if not smtp.get("smtp_host"):
            log.warning("SMTP not configured — skipping email notification.")
            return

        recipients = self._get_recipients(smtp)
        if not recipients:
            log.warning("No email recipients configured — skipping.")
            return

        color = _SEV_COLOR.get(severity, "#999")
        label = _SEV_LABEL.get(severity, severity)
        subject = f"{prefix} [{label}] {name} — {source}".strip()

        body_html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:20px">
<div style="max-width:620px;margin:auto;background:#161b22;border-radius:8px;overflow:hidden;
            border-left:6px solid {color}">
  <div style="background:{color};padding:14px 20px">
    <h2 style="margin:0;color:#fff;font-size:18px">{label}: {name}</h2>
  </div>
  <div style="padding:20px">
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e;width:120px">Source</td>
        <td style="padding:8px 4px">{source}</td>
      </tr>
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e">Severity</td>
        <td style="padding:8px 4px;color:{color}"><strong>{severity}</strong></td>
      </tr>
      <tr>
        <td style="padding:8px 4px;color:#8b949e">Time (UTC)</td>
        <td style="padding:8px 4px">{ts}</td>
      </tr>
    </table>

    <h3 style="font-size:13px;color:#8b949e;margin:20px 0 6px">DETAIL</h3>
    <pre style="background:#0d1117;padding:12px;border-radius:4px;font-size:13px;
                color:#c9d1d9;white-space:pre-wrap;margin:0">{detail or "(none)"}</pre>

    <h3 style="font-size:13px;color:#8b949e;margin:20px 0 6px">SUGGESTED REMEDIATION</h3>
    <pre style="background:#0d1117;padding:12px;border-radius:4px;font-size:13px;
                color:#58a6ff;white-space:pre-wrap;margin:0">{remediation or "(none)"}</pre>

    <div style="margin-top:20px;padding-top:16px;border-top:1px solid #30363d;
                font-size:12px;color:#6e7681">
      FPREN — Florida Public Radio Emergency Network<br>
      <a href="{DASHBOARD_URL}" style="color:#58a6ff">View Alarm Dashboard</a>
    </div>
  </div>
</div>
</body></html>"""

        self._smtp_send(smtp, subject, body_html, recipients,
                        log_tag=f"[{severity}] {name}")

    def _send_clear_email(self, severity, name, source, clear_detail, duration_str, ts):
        if not ALARM_EMAIL_ENABLED:
            return
        smtp = _load_smtp()
        if not smtp.get("smtp_host"):
            return
        recipients = self._get_recipients(smtp)
        if not recipients:
            return

        orig_color = _SEV_COLOR.get(severity, "#999")
        orig_label = _SEV_LABEL.get(severity, severity)
        duration_row = ""
        if duration_str:
            duration_row = f"""
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e">Duration</td>
        <td style="padding:8px 4px">{duration_str}</td>
      </tr>"""

        subject = f"[RESOLVED] {name} — {source}"
        body_html = f"""<!DOCTYPE html>
<html><body style="font-family:Arial,sans-serif;background:#0d1117;color:#c9d1d9;margin:0;padding:20px">
<div style="max-width:620px;margin:auto;background:#161b22;border-radius:8px;overflow:hidden;
            border-left:6px solid {_RESOLVED_COLOR}">
  <div style="background:{_RESOLVED_COLOR};padding:14px 20px">
    <h2 style="margin:0;color:#fff;font-size:18px">RESOLVED: {name}</h2>
  </div>
  <div style="padding:20px">
    <table style="width:100%;border-collapse:collapse;font-size:14px">
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e;width:120px">Source</td>
        <td style="padding:8px 4px">{source}</td>
      </tr>
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e">Original Severity</td>
        <td style="padding:8px 4px;color:{orig_color}">{orig_label}</td>
      </tr>
      <tr style="border-bottom:1px solid #30363d">
        <td style="padding:8px 4px;color:#8b949e">Cleared At (UTC)</td>
        <td style="padding:8px 4px">{ts}</td>
      </tr>{duration_row}
    </table>

    <h3 style="font-size:13px;color:#8b949e;margin:20px 0 6px">CLEAR REASON</h3>
    <pre style="background:#0d1117;padding:12px;border-radius:4px;font-size:13px;
                color:#c9d1d9;white-space:pre-wrap;margin:0">{clear_detail or "(auto-cleared)"}</pre>

    <div style="margin-top:20px;padding-top:16px;border-top:1px solid #30363d;
                font-size:12px;color:#6e7681">
      FPREN — Florida Public Radio Emergency Network<br>
      <a href="{DASHBOARD_URL}" style="color:#58a6ff">View Alarm Dashboard</a>
    </div>
  </div>
</div>
</body></html>"""

        self._smtp_send(smtp, subject, body_html, recipients,
                        log_tag=f"[RESOLVED] {name}")

    def _smtp_send(self, smtp: dict, subject: str, body_html: str,
                   recipients: list, log_tag: str):
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = smtp.get("mail_from", "fpren-alarms@localhost")
            msg["To"]      = ", ".join(recipients)
            msg.attach(MIMEText(body_html, "html"))

            with smtplib.SMTP(smtp["smtp_host"], int(smtp.get("smtp_port", 587)), timeout=15) as srv:
                if smtp.get("use_tls", True):
                    srv.starttls()
                if smtp.get("use_auth", True) and smtp.get("smtp_user"):
                    srv.login(smtp["smtp_user"], smtp.get("smtp_pass", ""))
                srv.sendmail(msg["From"], recipients, msg.as_string())
            log.info("Alarm email sent %s", log_tag)
        except Exception as exc:
            log.error("Alarm email failed %s: %s", log_tag, exc)

    # ── SMS ───────────────────────────────────────────────────────────────────

    def _send_sms(self, severity, name, source, detail, remediation, ts, prefix):
        numbers = [n.strip() for n in TWILIO_ALARM_NUMBERS.split(",") if n.strip()]
        if not numbers:
            log.warning("TWILIO_ALARM_NUMBERS not set — skipping SMS.")
            return
        if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER]):
            log.warning("Twilio credentials incomplete — skipping SMS.")
            return

        label = _SEV_LABEL.get(severity, severity)
        body = (
            f"{prefix} [{label}] {name}\n"
            f"Source: {source}\n"
            f"Time: {ts}\n"
            f"{(detail or '')[:140]}\n"
            f"Remedy: {(remediation or '')[:120]}\n"
            f"Dashboard: {DASHBOARD_URL}"
        ).strip()[:1600]

        try:
            from twilio.rest import Client
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            for number in numbers:
                client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=number)
                log.info("Alarm SMS sent to %s [%s] %s", number, severity, name)
        except ImportError:
            log.error("twilio package not installed — run: pip install twilio")
        except Exception as exc:
            log.error("Alarm SMS failed: %s", exc)
