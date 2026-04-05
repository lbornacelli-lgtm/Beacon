#!/usr/bin/env python3
"""Email helper called by Shiny app to avoid R/emayili namespace conflicts.

Usage: python3 send_email.py <json_config_file> <html_body_file>

json_config_file must contain: to, subject, mail_from, smtp_host, smtp_port
Optional fields: use_tls, use_auth, smtp_user, smtp_pass, attachment (file path)
"""
import json
import smtplib
import sys
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

if len(sys.argv) != 3:
    print("ERROR: usage: send_email.py <cfg.json> <body.html>", file=sys.stderr)
    sys.exit(1)

cfg_file, html_file = sys.argv[1], sys.argv[2]

with open(cfg_file) as f:
    cfg = json.load(f)

with open(html_file, encoding="utf-8") as f:
    html_body = f.read()

attachment_path = cfg.get("attachment", "")
has_attachment  = bool(attachment_path) and os.path.isfile(attachment_path)

# Use "mixed" when there's an attachment, "alternative" for HTML-only
msg = MIMEMultipart("mixed" if has_attachment else "alternative")
msg["Subject"] = cfg["subject"]
msg["From"]    = cfg["mail_from"]
msg["To"]      = cfg["to"]

# Wrap HTML in an alternative part so clients prefer it over plain text
html_part = MIMEMultipart("alternative")
html_part.attach(MIMEText(html_body, "html", "utf-8"))
msg.attach(html_part)

if has_attachment:
    with open(attachment_path, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    fname = os.path.basename(attachment_path)
    part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
    part.add_header("Content-Type", "application/pdf")
    msg.attach(part)

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
