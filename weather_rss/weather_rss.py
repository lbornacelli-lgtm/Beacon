#!/usr/bin/env python3
import requests
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

# Directory to save XML files — override with OUTPUT_DIR env var for Docker
output_dir = os.environ.get("OUTPUT_DIR", "/home/lh_admin/weather_rss/feeds")
os.makedirs(output_dir, exist_ok=True)

# Florida ASOS stations — NOAA current_obs XML (updates ~hourly at source)
rss_feeds = {
    # North Florida
    "Gainesville":       "https://w1.weather.gov/xml/current_obs/KGNV.xml",
    "Ocala":             "https://w1.weather.gov/xml/current_obs/KOCF.xml",
    "Palatka":           "https://w1.weather.gov/xml/current_obs/KPAK.xml",
    "Jacksonville":      "https://w1.weather.gov/xml/current_obs/KJAX.xml",
    "Tallahassee":       "https://w1.weather.gov/xml/current_obs/KTLH.xml",
    "Pensacola":         "https://w1.weather.gov/xml/current_obs/KPNS.xml",
    "Panama_City":       "https://w1.weather.gov/xml/current_obs/KECP.xml",
    # Central Florida
    "Orlando":           "https://w1.weather.gov/xml/current_obs/KMCO.xml",
    "Daytona_Beach":     "https://w1.weather.gov/xml/current_obs/KDAB.xml",
    "Tampa":             "https://w1.weather.gov/xml/current_obs/KTPA.xml",
    "Sarasota":          "https://w1.weather.gov/xml/current_obs/KSRQ.xml",
    "Lakeland":          "https://w1.weather.gov/xml/current_obs/KLAL.xml",
    # South Florida
    "Fort_Myers":        "https://w1.weather.gov/xml/current_obs/KRSW.xml",
    "Fort_Lauderdale":   "https://w1.weather.gov/xml/current_obs/KFLL.xml",
    "Miami":             "https://w1.weather.gov/xml/current_obs/KMIA.xml",
    "West_Palm_Beach":   "https://w1.weather.gov/xml/current_obs/KPBI.xml",
    "Key_West":          "https://w1.weather.gov/xml/current_obs/KEYW.xml",
    # West Coast
    "St_Petersburg":     "https://w1.weather.gov/xml/current_obs/KSPG.xml",
    "Naples":            "https://w1.weather.gov/xml/current_obs/KAPF.xml",
}

# NOTE: NOAA current_obs XML updates ~every 60 minutes at the source.
# Fetching more often than that returns identical data. 15 minutes is a
# reasonable polling cadence — it catches the update shortly after it lands.
FETCH_INTERVAL = 900  # 15 minutes
KEEP_DAYS = 7

# Track last seen observation_time per station to avoid duplicate files
_last_obs_time: dict[str, str] = {}


def get_observation_time(xml_bytes: bytes) -> str | None:
    """Extract <observation_time_rfc822> from NOAA current_obs XML."""
    try:
        root = ET.fromstring(xml_bytes)
        el = root.find("observation_time_rfc822")
        return el.text.strip() if el is not None and el.text else None
    except ET.ParseError:
        return None


def cleanup_old_files():
    now = datetime.now()
    cutoff = now - timedelta(days=KEEP_DAYS)
    for filename in os.listdir(output_dir):
        if filename.endswith(".xml"):
            file_path = os.path.join(output_dir, filename)
            if datetime.fromtimestamp(os.path.getmtime(file_path)) < cutoff:
                try:
                    os.remove(file_path)
                    print(f"Deleted old file: {file_path}")
                except Exception as e:
                    print(f"Error deleting {file_path}: {e}")


while True:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved = 0
    skipped = 0

    for city, url in rss_feeds.items():
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            content = response.content

            obs_time = get_observation_time(content)

            # Skip if observation hasn't changed since last fetch
            if obs_time and _last_obs_time.get(city) == obs_time:
                skipped += 1
                continue

            _last_obs_time[city] = obs_time or timestamp
            filename = os.path.join(output_dir, f"{city}_{timestamp}.xml")
            with open(filename, "wb") as f:
                f.write(content)
            print(f"[{timestamp}] Saved {city} — obs_time: {obs_time}")
            saved += 1

        except Exception as e:
            print(f"[{timestamp}] Error fetching {city}: {e}")

    print(f"[{timestamp}] Cycle complete: {saved} saved, {skipped} skipped (unchanged)")
    cleanup_old_files()
    time.sleep(FETCH_INTERVAL)
