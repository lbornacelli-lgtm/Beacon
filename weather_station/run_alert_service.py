#!/usr/bin/env python3
"""
Beacon alert-processor background service entry point.

Runs continuously:
  - Converts new NWS alerts to WAV every 30 seconds
  - Routes WAV files to audio_playlist/alerts/<type>/ subfolders
  - Deletes WAV files older than 3 days (once per day)
  - Logs all deletions to weather_station/logs/wav_deletions.jsonl

Usage:
    cd /home/ufuser/Downloads/Beacon-main/weather_station
    source venv/bin/activate
    python run_alert_service.py
"""
import sys
import os
import logging

# Ensure imports resolve from weather_station/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOG_FILE = os.environ.get(
    "ALERT_SERVICE_LOG",
    "/home/ufuser/Downloads/Beacon-main/weather_station/logs/alert_service.log",
)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)

from config.settings import Settings
from services.alert_service import AlertService

if __name__ == "__main__":
    settings = Settings()
    svc = AlertService(settings)
    svc.run()
