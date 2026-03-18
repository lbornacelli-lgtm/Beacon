import json
import logging
import os
import smtplib
from email.message import EmailMessage

LOG_FILE    = os.environ.get("LOG_FILE", "/home/ufuser/Downloads/Beacon-main/weather_rss/logs/email.log")
SMTP_CFG    = os.environ.get("SMTP_CFG", "/home/ufuser/Downloads/Beacon-main/weather_rss/config/smtp_config.json")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def _load_cfg() -> dict:
    try:
        with open(SMTP_CFG) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def send_email(subject: str, body: str, to: str = None):
    """Send an email using settings from smtp_config.json."""
    cfg = _load_cfg()

    host     = cfg.get("smtp_host", "localhost")
    port     = int(cfg.get("smtp_port", 25))
    use_tls  = cfg.get("use_tls", False)
    use_auth = cfg.get("use_auth", False)
    user     = cfg.get("smtp_user") or os.environ.get("EMAIL_USER", "")
    passwd   = cfg.get("smtp_pass") or os.environ.get("EMAIL_PASS", "")
    mail_from = cfg.get("mail_from") or user
    mail_to   = to or cfg.get("mail_to", "")

    if not mail_to:
        logging.warning("No recipient configured — email not sent.")
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = mail_to
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
        logging.info("Email sent: %s → %s", subject, mail_to)
    except Exception as exc:
        logging.error("Failed to send email '%s': %s", subject, exc)
        raise


# Legacy wrapper kept for backwards compatibility
def send_success_email():
    send_email("Weather RSS Success", "Weather RSS feed processed successfully.")
