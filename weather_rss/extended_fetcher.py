#!/usr/bin/env python3
"""
extended_fetcher.py — Extended data fetcher for the Weather Station broadcast system.

Four independent daemon-thread polling loops:
  A. airport_metar_loop     — METAR data for 20 airports (FL + ATL + CLT) every 15 min
  B. nws_alerts_extended_loop — NWS active alerts for GA and NC every 30 sec
  C. fl_traffic_loop        — FL 511 traffic incidents every 5 min
  D. school_closings_loop   — Alachua County school closings/delays every 30 min
"""

import hashlib
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient

# -------------------------------
# CONFIGURATION
# -------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "weather_rss"

LOG_FILE = os.environ.get("LOG_FILE", "/home/ufuser/weather_rss/logs/extended_fetcher.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

# Loop intervals
METAR_INTERVAL          = 900   # 15 minutes
ALERTS_EXTENDED_INTERVAL = 30   # 30 seconds
TRAFFIC_INTERVAL        = 300   # 5 minutes
SCHOOL_INTERVAL         = 1800  # 30 minutes

# Aviation Weather API — METAR for 20 airports
METAR_URL = (
    "https://aviationweather.gov/api/data/metar"
    "?format=json"
    "&ids=KATL,KPDK,KFTY,KCLT,KJQF,"
    "KGNV,KOCF,KJAX,KTLH,KTPA,KMCO,KRSW,KMIA,KFLL,KPNS,KDAB,KEYW,KSRQ,KLAL,KAPF"
)

# NWS active alerts by state
NWS_ALERTS_GA_URL = "https://api.weather.gov/alerts/active?area=GA"
NWS_ALERTS_NC_URL = "https://api.weather.gov/alerts/active?area=NC"

# FL 511 traffic incidents — DataTables POST endpoint powering the fl511.com incident list
FL511_URL = "https://fl511.com/List/GetData/traffic"
FL511_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://fl511.com/List/Events/Traffic",
}
FL511_POST_BODY = "draw=1&start=0&length=2000"

# Alachua County Schools live feed
SCHOOL_URL = "https://www.alachuaschools.net/live-feed"

# Keywords that indicate a closure/delay post
CLOSURE_KEYWORDS = re.compile(
    r"\b(closed|closing|delay|delayed|early release|early dismissal|cancelled|canceled)\b",
    re.IGNORECASE,
)

CLOSURE_TYPE_MAP = [
    (re.compile(r"\bclosed\b|\bclosing\b", re.IGNORECASE), "closed"),
    (re.compile(r"\bdelay\b|\bdelayed\b", re.IGNORECASE), "delay"),
    (re.compile(r"\bearly release\b|\bearly dismissal\b", re.IGNORECASE), "early_release"),
    (re.compile(r"\bcancell?ed\b", re.IGNORECASE), "cancelled"),
]

# -------------------------------
# LOGGING
# -------------------------------
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# -------------------------------
# MONGODB SETUP
# -------------------------------
mongo = MongoClient(MONGO_URI)
db = mongo[DB_NAME]
airport_metar_col      = db.airport_metar
nws_alerts_ext_col     = db.nws_alerts_extended
fl_traffic_col         = db.fl_traffic
school_closings_col    = db.school_closings


# ===========================
# LOOP A — AIRPORT METAR
# ===========================
def _unix_to_iso(ts):
    """Convert a Unix timestamp (int) to an ISO 8601 string in UTC, or return as-is if not int."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    return ts


def fetch_airport_metar():
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(METAR_URL, timeout=20, headers={"User-Agent": "WeatherStation/1.0"})
        r.raise_for_status()
        records = r.json()

        updated = 0
        for rec in records:
            icao_id = rec.get("icaoId")
            if not icao_id:
                continue

            airport_metar_col.update_one(
                {"icaoId": icao_id},
                {"$set": {
                    "icaoId":    icao_id,
                    "name":      rec.get("name"),
                    "temp":      rec.get("temp"),
                    "dewp":      rec.get("dewp"),
                    "wdir":      rec.get("wdir"),
                    "wspd":      rec.get("wspd"),
                    "visib":     rec.get("visib"),
                    "altim":     rec.get("altim"),
                    "clouds":    rec.get("clouds"),
                    "fltCat":    rec.get("fltCat"),
                    "rawOb":     rec.get("rawOb"),
                    "obsTime":   _unix_to_iso(rec.get("obsTime")),
                    "lat":       rec.get("lat"),
                    "lon":       rec.get("lon"),
                    "fetched_at": now,
                }},
                upsert=True,
            )
            updated += 1

        logging.info(f"METAR: updated {updated} airport records")

    except Exception as e:
        logging.error(f"METAR fetch failed: {e}")


def airport_metar_loop():
    logging.info("Loop A started: airport_metar_loop (15-min interval)")
    while True:
        fetch_airport_metar()
        time.sleep(METAR_INTERVAL)


# ===========================
# LOOP B — NWS ALERTS GA + NC
# ===========================
def fetch_nws_alerts_for_state(url, state):
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "WeatherStation/1.0"})
        r.raise_for_status()
        data = r.json()
        alerts = data.get("features", [])

        new_count = 0
        for alert in alerts:
            props = alert.get("properties", {})
            alert_id = props.get("id")
            if not alert_id:
                continue

            result = nws_alerts_ext_col.update_one(
                {"alert_id": alert_id},
                {"$setOnInsert": {
                    "alert_id":   alert_id,
                    "state":      state,
                    "event":      props.get("event"),
                    "severity":   props.get("severity"),
                    "area_desc":  props.get("areaDesc"),
                    "headline":   props.get("headline"),
                    "description": props.get("description"),
                    "instruction": props.get("instruction"),
                    "start":      props.get("onset"),
                    "end":        props.get("ends"),
                    "fetched_at": now,
                }},
                upsert=True,
            )
            if result.upserted_id:
                new_count += 1

        if new_count:
            logging.info(f"Alerts {state}: stored {new_count} NEW (total active: {len(alerts)})")
        else:
            logging.debug(f"Alerts {state}: no new alerts ({len(alerts)} active, all stored)")

    except Exception as e:
        logging.error(f"NWS alerts fetch failed ({state}): {e}")


def nws_alerts_extended_loop():
    logging.info("Loop B started: nws_alerts_extended_loop (30-sec interval, GA + NC)")
    while True:
        fetch_nws_alerts_for_state(NWS_ALERTS_GA_URL, "GA")
        fetch_nws_alerts_for_state(NWS_ALERTS_NC_URL, "NC")
        time.sleep(ALERTS_EXTENDED_INTERVAL)


# ===========================
# LOOP C — FL 511 TRAFFIC
# ===========================
def fetch_fl_traffic():
    now = datetime.now(timezone.utc)
    try:
        r = requests.post(
            FL511_URL,
            data=FL511_POST_BODY,
            headers=FL511_HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
        incidents = payload.get("data", [])

        updated = 0
        for inc in incidents:
            incident_id = str(inc.get("id") or inc.get("sourceId") or "")
            if not incident_id:
                raw = f"{inc.get('type','')}{inc.get('roadwayName','')}{inc.get('locationDescription','')}"
                incident_id = hashlib.md5(raw.encode()).hexdigest()

            fl_traffic_col.update_one(
                {"incident_id": incident_id},
                {"$set": {
                    "incident_id":       incident_id,
                    "type":              inc.get("type"),
                    "event_subtype":     inc.get("eventSubType"),
                    "description":       inc.get("description"),
                    "location":          inc.get("locationDescription"),
                    "road":              inc.get("roadwayName"),
                    "direction":         inc.get("direction"),
                    "county":            inc.get("county"),
                    "dot_district":      inc.get("dotDistrict"),
                    "severity":          inc.get("severity"),
                    "is_full_closure":   inc.get("isFullClosure"),
                    "major_event":       inc.get("majorEvent"),
                    "lane_description":  inc.get("laneDescription"),
                    "start_time":        inc.get("startDate"),
                    "end_time":          inc.get("endDate"),
                    "last_updated":      inc.get("lastUpdated"),
                    "fetched_at":        now,
                }},
                upsert=True,
            )
            updated += 1

        logging.info(f"FL511: updated {updated} traffic incidents")

    except Exception as e:
        logging.error(f"FL511 fetch failed: {e}")


def fl_traffic_loop():
    logging.info("Loop C started: fl_traffic_loop (5-min interval)")
    while True:
        fetch_fl_traffic()
        time.sleep(TRAFFIC_INTERVAL)


# ===========================
# LOOP D — SCHOOL CLOSINGS
# ===========================
def _closure_type(text):
    for pattern, label in CLOSURE_TYPE_MAP:
        if pattern.search(text):
            return label
    return "other"


def _post_id(title, date_str):
    raw = f"{title}{date_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def fetch_school_closings():
    now = datetime.now(timezone.utc)
    try:
        r = requests.get(
            SCHOOL_URL,
            timeout=20,
            headers={"User-Agent": "WeatherStation/1.0"},
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        # Try common blog/news post selectors — adjust if site layout changes
        candidates = (
            soup.select("article")
            or soup.select(".post")
            or soup.select(".entry")
            or soup.select(".news-item")
            or soup.select(".feed-item")
            or soup.select("li")  # fallback
        )

        new_count = 0
        for item in candidates:
            title_el = (
                item.find(["h1", "h2", "h3", "h4"])
                or item.find(class_=re.compile(r"title|heading", re.IGNORECASE))
            )
            title = title_el.get_text(strip=True) if title_el else item.get_text(" ", strip=True)[:120]

            if not CLOSURE_KEYWORDS.search(title):
                # Also check body text briefly
                body_text = item.get_text(" ", strip=True)
                if not CLOSURE_KEYWORDS.search(body_text):
                    continue

            date_el = item.find(["time", "span"], class_=re.compile(r"date|time|published", re.IGNORECASE))
            if not date_el:
                date_el = item.find("time")
            published_date = (
                date_el.get("datetime") or date_el.get_text(strip=True)
                if date_el else now.isoformat()
            )

            content = item.get_text(" ", strip=True)
            post_id = _post_id(title, published_date)

            result = school_closings_col.update_one(
                {"post_id": post_id},
                {"$setOnInsert": {
                    "post_id":       post_id,
                    "title":         title,
                    "content":       content[:2000],
                    "published_date": published_date,
                    "closure_type":  _closure_type(title + " " + content),
                    "fetched_at":    now,
                }},
                upsert=True,
            )
            if result.upserted_id:
                new_count += 1

        logging.info(f"School closings: found {new_count} NEW closure/delay posts")

    except Exception as e:
        logging.error(f"School closings fetch failed: {e}")


def school_closings_loop():
    logging.info("Loop D started: school_closings_loop (30-min interval)")
    while True:
        fetch_school_closings()
        time.sleep(SCHOOL_INTERVAL)


# -------------------------------
# ENTRY POINT
# -------------------------------
if __name__ == "__main__":
    logging.info("extended_fetcher starting — launching 4 loops")

    for target in [
        airport_metar_loop,
        nws_alerts_extended_loop,
        fl_traffic_loop,
        school_closings_loop,
    ]:
        threading.Thread(target=target, daemon=True, name=target.__name__).start()

    # Keep the main thread alive so daemon threads continue running
    while True:
        time.sleep(60)
