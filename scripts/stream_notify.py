#!/usr/bin/env python3
"""
stream_notify.py  —  Send stream event notifications via email, SMS, or phone call.

Usage:
  python3 stream_notify.py <event>
  event: "offline" | "online" | "reboot"
"""

import json
import logging
import os
import sys
import smtplib
from datetime import datetime
from email.message import EmailMessage

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "stream_notify_config.json")
SMTP_CONFIG = "/home/ufuser/Fpren-main/weather_rss/config/smtp_config.json"
LOG_FILE    = "/home/ufuser/Fpren-main/logs/stream_notifications.log"

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        logging.warning("Could not load %s: %s", path, e)
        return {}


def send_email(subject, body, cfg, smtp_cfg):
    to_addr = cfg.get("email", "")
    if not to_addr:
        logging.warning("Email enabled but no address configured.")
        return

    host      = smtp_cfg.get("smtp_host", "localhost")
    port      = int(smtp_cfg.get("smtp_port", 25))
    use_tls   = smtp_cfg.get("use_tls", False)
    use_auth  = smtp_cfg.get("use_auth", False)
    user      = smtp_cfg.get("smtp_user", "")
    passwd    = smtp_cfg.get("smtp_pass", "")
    mail_from = smtp_cfg.get("mail_from", to_addr)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = to_addr
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls()
                smtp.ehlo()
            if use_auth and user and passwd:
                smtp.login(user, passwd)
            smtp.send_message(msg)
        logging.info("Email sent: %s -> %s", subject, to_addr)
    except Exception as e:
        logging.error("Email failed: %s", e)


def send_sms(body, cfg):
    import urllib.request, urllib.parse, base64

    sid   = cfg.get("twilio_sid", "")
    token = cfg.get("twilio_token", "")
    from_ = cfg.get("twilio_from", "")
    to    = cfg.get("phone", "")

    if not all([sid, token, from_, to]):
        logging.warning("SMS enabled but Twilio credentials or phone number not configured.")
        return

    url  = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({"From": from_, "To": to, "Body": body}).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    creds = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logging.info("SMS sent to %s (status %s)", to, resp.status)
    except Exception as e:
        logging.error("SMS failed: %s", e)


def send_phone_call(message, cfg):
    import urllib.request, urllib.parse, base64

    sid   = cfg.get("twilio_sid", "")
    token = cfg.get("twilio_token", "")
    from_ = cfg.get("twilio_from", "")
    to    = cfg.get("phone", "")

    if not all([sid, token, from_, to]):
        logging.warning("Phone call enabled but Twilio credentials or phone number not configured.")
        return

    twiml = f"<Response><Say voice='alice'>{message}</Say></Response>"
    url   = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    twiml_url = (
        "http://twimlets.com/echo?Twiml=" + urllib.parse.quote(twiml)
    )
    data = urllib.parse.urlencode({"From": from_, "To": to, "Url": twiml_url}).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    creds = base64.b64encode(f"{sid}:{token}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logging.info("Phone call initiated to %s (status %s)", to, resp.status)
    except Exception as e:
        logging.error("Phone call failed: %s", e)


EVENT_MESSAGES = {
    "offline": {
        "subject": "[FPREN ALERT] Icecast Stream OFFLINE (port 8000)",
        "body":    "The FPREN Icecast stream on port 8000 has gone OFFLINE.\nTime: {ts}\nServer: {host}",
        "sms":     "FPREN ALERT: Icecast stream port 8000 is OFFLINE as of {ts}.",
        "voice":   "Attention. The F P R E N Icecast stream on port 8000 is offline as of {ts}. Please check the server.",
    },
    "online": {
        "subject": "[FPREN] Icecast Stream RESTORED (port 8000)",
        "body":    "The FPREN Icecast stream on port 8000 is back ONLINE.\nTime: {ts}\nServer: {host}",
        "sms":     "FPREN: Icecast stream port 8000 is back ONLINE as of {ts}.",
        "voice":   "The F P R E N Icecast stream on port 8000 is back online as of {ts}.",
    },
    "reboot": {
        "subject": "[FPREN] Server Rebooted",
        "body":    "The FPREN server has rebooted.\nBoot time: {ts}\nServer: {host}",
        "sms":     "FPREN: Server rebooted at {ts}.",
        "voice":   "The F P R E N server has rebooted at {ts}. Services are coming back online.",
    },
}


def main():
    if len(sys.argv) < 2:
        print("Usage: stream_notify.py <offline|online|reboot>")
        sys.exit(1)

    event = sys.argv[1].lower()
    if event not in EVENT_MESSAGES:
        print(f"Unknown event: {event}")
        sys.exit(1)

    cfg      = load_json(CONFIG_FILE)
    smtp_cfg = load_json(SMTP_CONFIG)
    methods  = cfg.get("notify_methods", ["email"])

    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    host = os.uname().nodename
    tmpl = EVENT_MESSAGES[event]

    subject = tmpl["subject"]
    body    = tmpl["body"].format(ts=ts, host=host)
    sms_msg = tmpl["sms"].format(ts=ts)
    voice_msg = tmpl["voice"].format(ts=ts)

    logging.info("Sending %s notification via: %s", event, methods)

    if "email" in methods:
        send_email(subject, body, cfg, smtp_cfg)

    if "sms" in methods:
        send_sms(sms_msg, cfg)

    if "phone" in methods:
        send_phone_call(voice_msg, cfg)


if __name__ == "__main__":
    main()
