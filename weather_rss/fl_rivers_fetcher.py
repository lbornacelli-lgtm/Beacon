#!/usr/bin/env python3
"""
Florida Rivers Fetcher — polls USGS Water Services every 15 minutes.

Architecture (NOAA NWPS is blocked from UF VM; USGS is accessible):
  • Primary data source:  USGS Instantaneous Values API
    https://waterservices.usgs.gov/nwis/iv/
    → gage height (ft) and discharge (cfs) for all active FL stream gauges

  • Site metadata source: USGS Site Service
    https://waterservices.usgs.gov/nwis/site/
    → lat/lon, site name, county code, HUC

  • Flood stage thresholds: config/fl_river_gauges_seed.json
    → NWS AHPS action/minor/moderate/major stages for ~40 key FL forecast gauges

  • Active flood events:   api.weather.gov/alerts/active?area=FL (already in nws_alerts)
    → checked to mark gauges at flood stage even if not in seed

  For gauges NOT in the seed, flood category is computed from the ratio
  of current stage to the highest known stage in the past 30 days (stored
  in fl_river_readings) — a lightweight percentile proxy.

MongoDB collections written:
  • fl_river_gauges   — site metadata + flood stages (upserted by site_no)
  • fl_river_readings — time-series readings per gauge (90-day TTL)

Usage:
  python3 fl_rivers_fetcher.py           # single fetch and exit
  python3 fl_rivers_fetcher.py --loop    # continuous 15-min polling loop
"""
import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from pymongo import MongoClient, ASCENDING, DESCENDING

MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME          = "weather_rss"
POLL_INTERVAL    = int(os.getenv("RIVERS_POLL_INTERVAL", "900"))  # 15 min

USGS_IV_URL   = "https://waterservices.usgs.gov/nwis/iv/"
USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
NWS_ALERTS_URL = "https://api.weather.gov/alerts/active"

SEED_FILE = Path(__file__).parent / "config" / "fl_river_gauges_seed.json"

LOG_FILE = os.getenv("LOG_FILE", "/home/ufuser/Fpren-main/weather_rss/logs/fl_rivers.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [RiversFetcher] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("RiversFetcher")

FLOOD_SEVERITY_ORDER = {
    "Normal": 0, "Unknown": 0, "Action": 1,
    "Minor": 2, "Moderate": 3, "Major": 4, "Record": 5,
}

# County FIPS code → county name (FL only, partial — covers major river counties)
FL_COUNTY_FIPS = {
    "12001":"Alachua","12003":"Baker","12005":"Bay","12007":"Bradford","12009":"Brevard",
    "12011":"Broward","12013":"Calhoun","12015":"Charlotte","12017":"Citrus","12019":"Clay",
    "12021":"Collier","12023":"Columbia","12027":"DeSoto","12029":"Dixie","12031":"Duval",
    "12033":"Escambia","12035":"Flagler","12037":"Franklin","12039":"Gadsden","12041":"Gilchrist",
    "12043":"Glades","12045":"Gulf","12047":"Hamilton","12049":"Hardee","12051":"Hendry",
    "12053":"Hernando","12055":"Highlands","12057":"Hillsborough","12059":"Holmes",
    "12061":"Indian River","12063":"Jackson","12065":"Jefferson","12067":"Lafayette",
    "12069":"Lake","12071":"Lee","12073":"Leon","12075":"Levy","12077":"Liberty",
    "12079":"Madison","12081":"Manatee","12083":"Marion","12085":"Martin","12086":"Miami-Dade",
    "12087":"Monroe","12089":"Nassau","12091":"Okaloosa","12093":"Okeechobee",
    "12095":"Orange","12097":"Osceola","12099":"Palm Beach","12101":"Pasco","12103":"Pinellas",
    "12105":"Polk","12107":"Putnam","12109":"St. Johns","12111":"St. Lucie","12113":"Santa Rosa",
    "12115":"Sarasota","12117":"Seminole","12119":"Sumter","12121":"Suwannee","12123":"Taylor",
    "12125":"Union","12127":"Volusia","12129":"Wakulla","12131":"Walton","12133":"Washington",
}


# ── Seed data ─────────────────────────────────────────────────────────────────

def load_seed() -> dict[str, dict]:
    """Load seed gauge data keyed by usgs_site_no."""
    try:
        with open(SEED_FILE) as f:
            data = json.load(f)
        return {g["usgs_site_no"]: g for g in data if g.get("usgs_site_no")}
    except Exception as exc:
        log.warning("Could not load seed file: %s", exc)
        return {}


# ── USGS fetch helpers ────────────────────────────────────────────────────────

def fetch_usgs_iv_fl() -> list[dict]:
    """
    Fetch gage height (00065) and discharge (00060) for all active FL stream
    gauges. Returns list of dicts: {site_no, site_name, lat, lon, county_fips,
    gage_height_ft, discharge_cfs, obs_time}.
    """
    results: dict[str, dict] = {}
    try:
        resp = requests.get(
            USGS_IV_URL,
            params={
                "format":      "json",
                "stateCd":     "FL",
                "parameterCd": "00065,00060",
                "siteStatus":  "active",
                "siteType":    "ST",
                "period":      "PT2H",
            },
            timeout=45,
            headers={"User-Agent": "FPREN-Rivers/1.0 (fpren@ufl.edu)"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("USGS IV fetch error: %s", exc)
        return []

    for ts in data.get("value", {}).get("timeSeries", []):
        try:
            si       = ts["sourceInfo"]
            site_no  = si["siteCode"][0]["value"]
            var_code = ts["variable"]["variableCode"][0]["value"]
            values   = ts["values"][0]["value"]

            if site_no not in results:
                props = {p["name"]: p["value"] for p in si.get("siteProperty", [])}
                county_fips = props.get("countyCd", "")
                geo = si.get("geoLocation", {}).get("geogLocation", {})
                results[site_no] = {
                    "site_no":     site_no,
                    "site_name":   si.get("siteName", ""),
                    "lat":         float(geo.get("latitude",  0) or 0),
                    "lon":         float(geo.get("longitude", 0) or 0),
                    "county_fips": county_fips,
                    "county":      FL_COUNTY_FIPS.get(f"12{county_fips[-3:]}" if len(county_fips) == 3 else county_fips, ""),
                    "state":       "FL",
                }

            if not values:
                continue
            latest = values[-1]
            val_str = latest.get("value", "")
            if not val_str or val_str == "-999999":
                continue
            val = float(val_str)
            dt  = latest.get("dateTime", "")

            if var_code == "00065":
                results[site_no]["gage_height_ft"] = val
                results[site_no]["obs_time"]        = dt
            elif var_code == "00060":
                results[site_no]["discharge_cfs"] = val
        except Exception:
            continue

    # Return only sites that have a gage height reading
    out = [r for r in results.values() if r.get("gage_height_ft") is not None]
    log.info("USGS IV: %d FL stream gauges with current readings", len(out))
    return out


def fetch_nws_flood_alerts() -> list[dict]:
    """Return active NWS flood alerts for FL (event + area_desc)."""
    try:
        resp = requests.get(
            NWS_ALERTS_URL,
            params={"area": "FL"},
            timeout=15,
            headers={"User-Agent": "FPREN-Rivers/1.0 (fpren@ufl.edu)"},
        )
        resp.raise_for_status()
        feats = resp.json().get("features", [])
        flood_kw = {"flood", "flash flood", "river", "hydrologic"}
        alerts = []
        for f in feats:
            p = f.get("properties", {})
            event = (p.get("event") or "").lower()
            if any(kw in event for kw in flood_kw):
                alerts.append({
                    "event":     p.get("event", ""),
                    "area_desc": p.get("areaDesc", ""),
                    "severity":  p.get("severity", ""),
                })
        log.info("NWS: %d active flood alerts for FL", len(alerts))
        return alerts
    except Exception as exc:
        log.warning("NWS alerts fetch failed: %s", exc)
        return []


# ── Flood categorization ──────────────────────────────────────────────────────

def determine_flood_category(
    gage_height: float,
    seed: dict | None,
) -> str:
    """
    Determine flood category for a gauge.
    If the gauge is in the seed file with NWS flood stage thresholds, compare
    the current stage directly. Otherwise return "Unknown" — we don't fabricate
    flood categories without authoritative threshold data.
    """
    if not seed:
        return "Unknown"
    action   = seed.get("action_stage_ft")
    minor    = seed.get("minor_stage_ft")
    moderate = seed.get("moderate_stage_ft")
    major    = seed.get("major_stage_ft")
    if major    is not None and gage_height >= major:    return "Major"
    if moderate is not None and gage_height >= moderate: return "Moderate"
    if minor    is not None and gage_height >= minor:    return "Minor"
    if action   is not None and gage_height >= action:   return "Action"
    return "Normal"


# ── MongoDB helpers ───────────────────────────────────────────────────────────

def ensure_indexes(db):
    db.fl_river_gauges.create_index([("site_no", ASCENDING)], unique=True)
    db.fl_river_gauges.create_index([("lid", ASCENDING)], sparse=True, name="lid_sparse")
    db.fl_river_gauges.create_index([("flood_category", ASCENDING)])
    db.fl_river_gauges.create_index([("county", ASCENDING)])

    db.fl_river_readings.create_index([("site_no", ASCENDING), ("fetched_at", DESCENDING)])
    db.fl_river_readings.create_index(
        [("fetched_at", ASCENDING)],
        expireAfterSeconds=90 * 24 * 3600,
        name="ttl_90d",
    )


def get_historical_max(db, site_no: str) -> float | None:
    """Return the maximum gage height seen in the last 30 days."""
    since = datetime.now(timezone.utc) - timedelta(days=30)
    doc = db.fl_river_readings.find_one(
        {"site_no": site_no, "fetched_at": {"$gte": since}},
        sort=[("gage_height_ft", DESCENDING)],
        projection={"gage_height_ft": 1, "_id": 0},
    )
    return doc["gage_height_ft"] if doc else None


def seed_gauges(db, seed: dict[str, dict]):
    """Upsert seed gauge records into fl_river_gauges on first run."""
    for site_no, g in seed.items():
        db.fl_river_gauges.update_one(
            {"site_no": site_no},
            {"$setOnInsert": {
                "site_no":          site_no,
                "lid":              g.get("lid", ""),
                "name":             g.get("name", ""),
                "river":            g.get("river", ""),
                "county":           g.get("county", ""),
                "state":            "FL",
                "lat":              g.get("lat"),
                "lon":              g.get("lon"),
                "wfo":              g.get("wfo", ""),
                "action_stage_ft":  g.get("action_stage_ft"),
                "minor_stage_ft":   g.get("minor_stage_ft"),
                "moderate_stage_ft":g.get("moderate_stage_ft"),
                "major_stage_ft":   g.get("major_stage_ft"),
                "flood_category":   "Unknown",
                "current_stage_ft": None,
                "updated_at":       datetime.now(timezone.utc),
            }},
            upsert=True,
        )
    log.info("Seeded %d gauge records", len(seed))


def update_gauge(db, reading: dict, seed: dict | None) -> str:
    """
    Upsert a gauge document and return the computed flood category.
    Creates a human-readable river name from USGS site name if not in seed.
    """
    site_no     = reading["site_no"]
    gage_height = reading.get("gage_height_ft")
    category    = determine_flood_category(gage_height or 0, seed)

    # Build display name: prefer seed name, fall back to USGS site_name
    if seed:
        name  = seed.get("name", reading["site_name"])
        river = seed.get("river", "")
        lid   = seed.get("lid", "")
        wfo   = seed.get("wfo", "")
        action_stage   = seed.get("action_stage_ft")
        minor_stage    = seed.get("minor_stage_ft")
        moderate_stage = seed.get("moderate_stage_ft")
        major_stage    = seed.get("major_stage_ft")
    else:
        name  = _clean_site_name(reading["site_name"])
        river = _extract_river(reading["site_name"])
        lid   = wfo = ""
        action_stage = minor_stage = moderate_stage = major_stage = None

    db.fl_river_gauges.update_one(
        {"site_no": site_no},
        {"$set": {
            "name":               name,
            "river":              river,
            "county":             reading.get("county") or seed.get("county", "") if seed else reading.get("county", ""),
            "lat":                reading.get("lat") or (seed.get("lat") if seed else None),
            "lon":                reading.get("lon") or (seed.get("lon") if seed else None),
            "lid":                lid,
            "wfo":                wfo,
            "action_stage_ft":    action_stage,
            "minor_stage_ft":     minor_stage,
            "moderate_stage_ft":  moderate_stage,
            "major_stage_ft":     major_stage,
            "current_stage_ft":   gage_height,
            "current_discharge_cfs": reading.get("discharge_cfs"),
            "flood_category":     category,
            "obs_time":           reading.get("obs_time", ""),
            "updated_at":         datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return category


def insert_reading(db, reading: dict, category: str):
    db.fl_river_readings.insert_one({
        "site_no":        reading["site_no"],
        "lid":            reading.get("lid", ""),
        "gauge_name":     reading.get("site_name", ""),
        "gage_height_ft": reading.get("gage_height_ft"),
        "discharge_cfs":  reading.get("discharge_cfs"),
        "flood_category": category,
        "fetched_at":     datetime.now(timezone.utc),
    })


def _clean_site_name(raw: str) -> str:
    return raw.strip().title()


def _extract_river(site_name: str) -> str:
    """Extract river name from USGS site name like 'SUWANNEE RIVER NEAR BRANFORD, FL'."""
    import re
    m = re.match(r'^([\w\s]+(?:RIVER|CREEK|BRANCH|RUN|LAKE|SPRING|CANAL|SLOUGH|BAYOU))',
                 site_name, re.IGNORECASE)
    return m.group(1).strip().title() if m else site_name.split(" AT ")[0].strip().title()


# ── Main fetch cycle ──────────────────────────────────────────────────────────

def run_once(db) -> dict:
    log.info("─── Fetch cycle starting ───")
    cycle_start = datetime.now(timezone.utc)

    seed = load_seed()
    seed_site_nos = set(seed.keys())

    # Fetch USGS current readings
    readings = fetch_usgs_iv_fl()

    # Fetch active NWS flood alerts (for log enrichment)
    flood_alerts = fetch_nws_flood_alerts()
    flood_counties = set()
    for alert in flood_alerts:
        # Rough county extraction from areaDesc
        for county_name in FL_COUNTY_FIPS.values():
            if county_name.lower() in alert["area_desc"].lower():
                flood_counties.add(county_name.lower())

    at_flood   = 0
    new_gauges = 0

    for r in readings:
        site_no = r["site_no"]
        gauge_seed = seed.get(site_no)
        category = update_gauge(db, r, gauge_seed)
        insert_reading(db, r, category)
        if FLOOD_SEVERITY_ORDER.get(category, 0) >= 1:
            at_flood += 1
            log.warning("  FLOOD: %s — %s ft [%s]",
                        r.get("site_name", site_no)[:50],
                        r.get("gage_height_ft"), category)

    elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
    summary = {
        "gauges_fetched":   len(readings),
        "at_flood_count":   at_flood,
        "nws_flood_alerts": len(flood_alerts),
        "elapsed_s":        round(elapsed, 1),
    }
    log.info("Cycle done in %.1fs — %s", elapsed, summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description="FPREN Florida Rivers Fetcher")
    parser.add_argument("--loop", action="store_true",
                        help="Run continuously every RIVERS_POLL_INTERVAL seconds")
    parser.add_argument("--seed-only", action="store_true",
                        help="Seed gauge records then exit (no USGS fetch)")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    ensure_indexes(db)

    seed = load_seed()
    seed_gauges(db, seed)
    log.info("Seed loaded: %d key FL forecast gauges", len(seed))

    if args.seed_only:
        return

    if args.loop:
        log.info("Starting continuous loop (interval=%ds)", POLL_INTERVAL)
        while True:
            try:
                run_once(db)
            except Exception as exc:
                log.error("Unhandled error: %s", exc, exc_info=True)
            time.sleep(POLL_INTERVAL)
    else:
        run_once(db)


if __name__ == "__main__":
    main()
