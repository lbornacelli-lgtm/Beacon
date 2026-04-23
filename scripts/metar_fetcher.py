#!/usr/bin/env python3
"""
scripts/metar_fetcher.py — Standalone METAR Aviation Weather ETL

Fetches METAR observations from api.weather.gov for Florida ASOS stations
(defaulting to KGNV — Gainesville Regional Airport) and upserts records
to MongoDB weather_rss.airport_metar alongside NWS data.

This is a manual/test ETL utility. The production service that runs
continuously is beacon-obs-fetcher (weather_rss/weather_rss.py).
Use this script for one-off fetches, KGNV-specific queries, or testing.

Usage:
    python3 scripts/metar_fetcher.py                   # KGNV only
    python3 scripts/metar_fetcher.py --stations all    # all 19 FL ASOS stations
    python3 scripts/metar_fetcher.py --stations KMIA,KTPA,KJAX
    python3 scripts/metar_fetcher.py --loop            # poll every POLL_INTERVAL seconds
    python3 scripts/metar_fetcher.py --dry-run         # parse only, no MongoDB writes
    python3 scripts/metar_fetcher.py --verbose         # show parsed records
"""
import argparse
import logging
import os
import time
from datetime import datetime, timezone

import requests
from pymongo import MongoClient, UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [metar_fetcher] %(levelname)s %(message)s",
)
log = logging.getLogger("metar_fetcher")

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URI     = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME       = "weather_rss"
COLLECTION    = "airport_metar"
POLL_INTERVAL = int(os.getenv("METAR_POLL_INTERVAL", "900"))   # 15 min default
REQUESTS_CA   = os.getenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
TIMEOUT       = 20

# 19 FL ASOS stations (matches production weather_rss.py)
FL_STATIONS = [
    "KJAX", "KTLH", "KGNV", "KOCF", "KMCO", "KDAB",
    "KTPA", "KSPG", "KSRQ", "KRSW", "KMIA", "KFLL",
    "KPBI", "KEYW", "KPNS", "KECP", "KMLB", "Ksfb", "KPIE",
]

# FL city mapping for display
STATION_CITY = {
    "KJAX": "Jacksonville", "KTLH": "Tallahassee", "KGNV": "Gainesville",
    "KOCF": "Ocala",        "KMCO": "Orlando",      "KDAB": "Daytona Beach",
    "KTPA": "Tampa",        "KSPG": "St. Petersburg","KSRQ": "Sarasota",
    "KRSW": "Fort Myers",   "KMIA": "Miami",         "KFLL": "Fort Lauderdale",
    "KPBI": "West Palm Beach","KEYW": "Key West",    "KPNS": "Pensacola",
    "KECP": "Panama City",  "KMLB": "Melbourne",     "KSFB": "Sanford",
    "KPIE": "Clearwater",
}

NWS_OBS_URL = "https://api.weather.gov/stations/{icao}/observations/latest"
NWS_HEADERS = {"User-Agent": "FPREN/1.0 (lbornacelli-lgtm@github.com)", "Accept": "application/geo+json"}


def _flight_category(visibility_m, ceiling_ft):
    """Compute flight category from visibility (meters) and ceiling (feet AGL)."""
    if visibility_m is None and ceiling_ft is None:
        return "UNK"
    vis_sm = (visibility_m or 0) / 1609.34
    ceil   = ceiling_ft or 99999
    if vis_sm < 1 or ceil < 500:
        return "LIFR"
    if vis_sm < 3 or ceil < 1000:
        return "IFR"
    if vis_sm < 5 or ceil < 3000:
        return "MVFR"
    return "VFR"


def _celsius_to_f(c):
    return round(c * 9 / 5 + 32, 1) if c is not None else None


def _pa_to_inhg(pa):
    return round(pa / 3386.389, 2) if pa is not None else None


def _mps_to_kt(mps):
    return round(mps * 1.94384, 1) if mps is not None else None


def fetch_metar(icao: str) -> dict | None:
    """Fetch latest METAR observation for a single station. Returns parsed dict or None."""
    url = NWS_OBS_URL.format(icao=icao)
    try:
        resp = requests.get(url, headers=NWS_HEADERS, timeout=TIMEOUT, verify=REQUESTS_CA)
        if resp.status_code == 404:
            log.warning("%s: station not found (404)", icao)
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("%s: fetch error: %s", icao, exc)
        return None

    props = data.get("properties", {})

    def _val(key):
        obj = props.get(key, {})
        return obj.get("value") if isinstance(obj, dict) else obj

    temp_c    = _val("temperature")
    dewpoint_c = _val("dewpoint")
    wind_spd   = _val("windSpeed")     # m/s
    wind_dir   = _val("windDirection")  # degrees
    wind_gust  = _val("windGust")      # m/s
    vis_m      = _val("visibility")    # meters
    pressure   = _val("seaLevelPressure")  # Pa
    cloud_layers = props.get("cloudLayers", [])

    # Compute ceiling from cloud layers
    ceiling_ft = None
    for layer in cloud_layers:
        amt    = layer.get("amount", "")
        base   = layer.get("base", {})
        height = base.get("value") if isinstance(base, dict) else None
        if height and amt in ("BKN", "OVC"):
            height_ft = round(height * 3.28084)
            if ceiling_ft is None or height_ft < ceiling_ft:
                ceiling_ft = height_ft

    flight_cat = _flight_category(vis_m, ceiling_ft)

    raw_time = props.get("timestamp")

    rec = {
        "icaoId":         icao,
        "city":           STATION_CITY.get(icao, icao),
        "raw_metar":      props.get("rawMessage", ""),
        "temp_c":         temp_c,
        "temp_f":         _celsius_to_f(temp_c),
        "dewpoint_c":     dewpoint_c,
        "dewpoint_f":     _celsius_to_f(dewpoint_c),
        "wind_dir":       wind_dir,
        "wind_speed_kt":  _mps_to_kt(wind_spd),
        "wind_gust_kt":   _mps_to_kt(wind_gust),
        "visibility_m":   vis_m,
        "visibility_sm":  round(vis_m / 1609.34, 1) if vis_m else None,
        "ceiling_ft":     ceiling_ft,
        "pressure_inhg":  _pa_to_inhg(pressure),
        "flight_category": flight_cat,
        "cloud_layers":   cloud_layers,
        "timestamp":      raw_time,
        "fetched_at":     datetime.now(timezone.utc),
        "source":         "nws_api",
        "processed":      False,
    }
    return rec


def upsert_records(col, records: list[dict], dry_run: bool = False) -> int:
    if not records:
        return 0
    if dry_run:
        log.info("[dry-run] would upsert %d records", len(records))
        return 0

    ops = [
        UpdateOne(
            {"icaoId": r["icaoId"]},
            {"$set": r},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops, ordered=False)
    log.info("Upserted %d METAR records", len(ops))
    return len(ops)


def run_once(stations: list[str], dry_run: bool = False, verbose: bool = False) -> int:
    client  = MongoClient(MONGO_URI)
    col     = client[DB_NAME][COLLECTION]
    records = []

    for icao in stations:
        rec = fetch_metar(icao)
        if rec:
            records.append(rec)
            if verbose:
                fc = rec.get("flight_category", "UNK")
                t  = rec.get("temp_f")
                ws = rec.get("wind_speed_kt")
                log.info("  %s (%s): %s°F, wind %skt, %s",
                         icao, rec.get("city", ""), t, ws, fc)

    written = upsert_records(col, records, dry_run=dry_run)
    client.close()
    return written


def _resolve_stations(arg: str) -> list[str]:
    if arg.upper() == "ALL":
        return FL_STATIONS
    return [s.strip().upper() for s in arg.split(",") if s.strip()]


def main():
    ap = argparse.ArgumentParser(description="Standalone METAR aviation weather ETL")
    ap.add_argument("--stations", default="KGNV",
                    help="Comma-separated ICAO codes or 'all' for 19 FL stations (default: KGNV)")
    ap.add_argument("--loop",    action="store_true", help="Loop every POLL_INTERVAL seconds")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, no MongoDB writes")
    ap.add_argument("--verbose", action="store_true", help="Print parsed records")
    args = ap.parse_args()

    stations = _resolve_stations(args.stations)
    log.info("Fetching METAR for: %s", stations)

    if args.loop:
        log.info("Starting loop — poll every %ds", POLL_INTERVAL)
        while True:
            try:
                run_once(stations, dry_run=args.dry_run, verbose=args.verbose)
            except Exception as exc:
                log.error("Run error: %s", exc)
            time.sleep(POLL_INTERVAL)
    else:
        n = run_once(stations, dry_run=args.dry_run, verbose=args.verbose)
        log.info("Done — %d records written", n)


if __name__ == "__main__":
    main()
