#!/usr/bin/env python3
import os
import requests
import feedparser
from pymongo import MongoClient
from datetime import datetime, timezone
import logging
import time
import threading

# -------------------------------
# CONFIGURATION
# -------------------------------
MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "weather_rss"
RSS_FEEDS = [
    {
        "name": "NHC Atlantic Tropical Weather",
        "url": "https://www.nhc.noaa.gov/index-at.xml",
        "filename": "nhc_atlantic.xml"
    },
    {
        "name": "NHC East Pacific Tropical Weather",
        "url": "https://www.nhc.noaa.gov/index-ep.xml",
        "filename": "nhc_eastpacific.xml"
    },
]

# Florida statewide alerts — replaces single lat/lon point
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active?area=FL"

ALERT_INTERVAL = 30    # seconds — alerts can be issued any time
RSS_INTERVAL   = 300   # seconds — NHC feeds update infrequently

LOG_FILE = os.environ.get("LOG_FILE", "/home/lh_admin/weather_rss/logs/service.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# -------------------------------
# LOGGING
# -------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# -------------------------------
# MONGODB SETUP
# -------------------------------
mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
rss_status_col = db.feed_status
rss_history_col = db.feed_history
nws_alerts_col = db.nws_alerts

# -------------------------------
# HELPER FUNCTIONS
# -------------------------------
def fetch_rss_feed(feed):
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(feed["url"], timeout=15)
        r.raise_for_status()
        content = r.content

        # Store feed content in MongoDB
        rss_status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "feed_name": feed["name"],
                "url": feed["url"],
                "last_fetch": now,
                "last_success": now,
                "status": "OK",
                "file_size_bytes": len(content),
                "error_message": None
            }, "$inc": {"update_count": 1}},
            upsert=True
        )

        rss_history_col.insert_one({
            "filename": feed["filename"],
            "timestamp": now,
            "status": "OK"
        })

        logging.info(f"Fetched RSS feed: {feed['name']} ({len(content)} bytes)")

    except Exception as e:
        logging.error(f"Failed to fetch RSS feed {feed['name']}: {e}")
        rss_status_col.update_one(
            {"filename": feed["filename"]},
            {"$set": {
                "last_fetch": now,
                "last_failure": now,
                "status": "ERROR",
                "error_message": str(e)
            }},
            upsert=True
        )
        rss_history_col.insert_one({
            "filename": feed["filename"],
            "timestamp": now,
            "status": "ERROR",
            "error": str(e)
        })

def fetch_nws_alerts():
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(NWS_ALERTS_URL, timeout=15,
                         headers={"User-Agent": "WeatherStation/1.0"})
        r.raise_for_status()
        data = r.json()
        alerts = data.get("features", [])

        new_count = 0
        for alert in alerts:
            properties = alert.get("properties", {})
            # Use the NWS-assigned ID — guaranteed unique per alert issuance
            alert_id = properties.get("id")
            if not alert_id:
                continue

            result = nws_alerts_col.update_one(
                {"alert_id": alert_id},
                {"$setOnInsert": {
                    "alert_id": alert_id,
                    "event": properties.get("event"),
                    "severity": properties.get("severity"),
                    "area_desc": properties.get("areaDesc"),
                    "headline": properties.get("headline"),
                    "description": properties.get("description"),
                    "instruction": properties.get("instruction"),
                    "start": properties.get("onset"),
                    "end": properties.get("ends"),
                    "fetched_at": now
                }},
                upsert=True
            )
            if result.upserted_id:
                new_count += 1

        if new_count:
            logging.info(f"Stored {new_count} NEW FL alerts (total active: {len(alerts)})")
        else:
            logging.debug(f"No new FL alerts ({len(alerts)} active, all already stored)")

    except Exception as e:
        logging.error(f"Failed to fetch NWS FL alerts: {e}")

# -------------------------------
# MAIN LOOPS (two independent cadences)
# -------------------------------
def alert_loop():
    """Poll NWS FL alerts every 30 seconds. Uses $setOnInsert to avoid duplicates."""
    logging.info("Alert loop started (30s interval, statewide FL)")
    while True:
        fetch_nws_alerts()
        time.sleep(ALERT_INTERVAL)

def rss_loop():
    """Fetch NHC RSS feeds every 5 minutes."""
    logging.info("RSS loop started (300s interval)")
    while True:
        for feed in RSS_FEEDS:
            fetch_rss_feed(feed)
        time.sleep(RSS_INTERVAL)

def main():
    logging.info("Weather service started")
    t_alerts = threading.Thread(target=alert_loop, daemon=True, name="alert-loop")
    t_rss    = threading.Thread(target=rss_loop,   daemon=True, name="rss-loop")
    t_alerts.start()
    t_rss.start()
    # Keep main thread alive
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
