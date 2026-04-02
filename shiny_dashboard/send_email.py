#!/usr/bin/env python3
"""Email helper called by Shiny app to avoid R/emayili namespace conflicts.

Usage: python3 send_email.py <json_config_file> <html_body_file>

json_config_file must contain: to, subject, mail_from, smtp_host, smtp_port
"""
import json
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

if len(sys.argv) != 3:
    print("ERROR: usage: send_email.py <cfg.json> <body.html>", file=sys.stderr)
    sys.exit(1)

cfg_file, html_file = sys.argv[1], sys.argv[2]

with open(cfg_file) as f:
    cfg = json.load(f)

with open(html_file, encoding="utf-8") as f:
    html_body = f.read()

msg = MIMEMultipart("alternative")
msg["Subject"] = cfg["subject"]
msg["From"]    = cfg["mail_from"]
msg["To"]      = cfg["to"]
msg.attach(MIMEText(html_body, "html", "utf-8"))

try:
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=15) as s:
        s.ehlo()
        if cfg.get("use_tls"):
            s.starttls()
            s.ehlo()
        if cfg.get("use_auth") and cfg.get("smtp_user"):
            s.login(cfg["smtp_user"], cfg.get("smtp_pass", ""))
        s.sendmail(cfg["mail_from"], cfg["to"], msg.as_string())
    print("OK")
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    sys.exit(1)
