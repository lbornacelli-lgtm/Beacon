#!/usr/bin/env python3
"""
stream_monitor.py  —  Continuously monitor Icecast stream on port 8000.

Checks every 60 seconds. On state change (online→offline or offline→online),
calls stream_notify.py to send configured notifications.
"""

import json
import logging
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

CONFIG_FILE   = os.path.join(os.path.dirname(__file__), "..", "stream_notify_config.json")
STATE_FILE    = "/home/ufuser/Fpren-main/logs/stream_state.txt"
LOG_FILE      = "/home/ufuser/Fpren-main/logs/stream_monitor.log"
NOTIFY_SCRIPT = os.path.join(os.path.dirname(__file__), "stream_notify.py")
CHECK_PORT    = 8000
CHECK_HOST    = "127.0.0.1"
INTERVAL      = 60  # seconds


os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)


def check_stream() -> bool:
    """Return True if something is listening on port 8000."""
    try:
        with socket.create_connection((CHECK_HOST, CHECK_PORT), timeout=5):
            return True
    except (ConnectionRefusedError, OSError, socket.timeout):
        return False


def read_last_state() -> str:
    """Return 'online', 'offline', or 'unknown'."""
    try:
        with open(STATE_FILE) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"


def write_state(state: str):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        f.write(state)


def send_notification(event: str):
    try:
        result = subprocess.run(
            [sys.executable, NOTIFY_SCRIPT, event],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            logging.error("Notify script error: %s", result.stderr)
        else:
            logging.info("Notification sent for event: %s", event)
    except Exception as e:
        logging.error("Failed to run notify script: %s", e)


def load_config() -> dict:
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    logging.info("Stream monitor started — watching port %d", CHECK_PORT)
    last_state = read_last_state()

    while True:
        try:
            is_up       = check_stream()
            curr_state  = "online" if is_up else "offline"
            cfg         = load_config()

            if last_state != curr_state:
                logging.info("Stream state changed: %s -> %s", last_state, curr_state)
                write_state(curr_state)

                if curr_state == "offline" and cfg.get("notify_on_offline", True):
                    send_notification("offline")
                elif curr_state == "online" and last_state not in ("unknown",):
                    # Only notify restoration if we previously saw it go down
                    send_notification("online")

                last_state = curr_state
            else:
                logging.debug("Stream state unchanged: %s", curr_state)

        except Exception as e:
            logging.error("Monitor loop error: %s", e)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
