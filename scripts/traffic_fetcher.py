#!/usr/bin/env python3
"""
scripts/traffic_fetcher.py — Standalone FL511 Traffic Incident ETL

Fetches Florida DOT FL511 traffic incidents via the public RSS/JSON feed
and upserts records to MongoDB weather_rss.fl_traffic.

This is a manual/test ETL utility. The production service that runs
continuously is beacon-extended-fetcher (weather_rss/extended_fetcher.py).
Use this script for one-off backfills, debugging, or testing.

Usage:
    python3 scripts/traffic_fetcher.py              # fetch once and exit
    python3 scripts/traffic_fetcher.py --loop       # loop every POLL_INTERVAL seconds
    python3 scripts/traffic_fetcher.py --dry-run    # parse only, no MongoDB writes
    python3 scripts/traffic_fetcher.py --county Alachua  # filter by county name
    python3 scripts/traffic_fetcher.py --verbose    # show parsed records
"""
import argparse
import hashlib
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests
from pymongo import MongoClient, UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [traffic_fetcher] %(levelname)s %(message)s",
)
log = logging.getLogger("traffic_fetcher")

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URI     = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME       = "weather_rss"
COLLECTION    = "fl_traffic"
POLL_INTERVAL = int(os.getenv("TRAFFIC_POLL_INTERVAL", "120"))
REQUESTS_CA   = os.getenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
TIMEOUT       = 20

# FL511 public incident feed (GeoJSON — no API key required)
FL511_JSON = "https://fl511.com/List/Incidents"

# FL511 RSS backup feed
FL511_RSS  = "https://fl511.com/rss/incidents.rss"


def _fetch_fl511_json() -> list[dict]:
    """Fetch FL511 incidents from JSON endpoint."""
    log.info("Fetching FL511 JSON: %s", FL511_JSON)
    try:
        resp = requests.get(FL511_JSON, timeout=TIMEOUT, verify=REQUESTS_CA)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("FL511 JSON fetch error: %s — trying RSS fallback", exc)
        return []

    records = []
    incidents = data if isinstance(data, list) else data.get("incidents", data.get("features", []))
    for item in incidents:
        if isinstance(item, dict) and "geometry" in item:
            # GeoJSON feature format
            props   = item.get("properties", item)
            coords  = item.get("geometry", {}).get("coordinates", [None, None])
            lon_val = coords[0] if coords else None
            lat_val = coords[1] if coords else None
        else:
            props   = item
            lon_val = item.get("longitude") or item.get("lon")
            lat_val = item.get("latitude")  or item.get("lat")

        incident_id = str(props.get("id", props.get("Id", props.get("incident_id", ""))))
        rec = {
            "incident_id": incident_id,
            "event_type":  props.get("type", props.get("eventType", props.get("Type", "Unknown"))),
            "description": props.get("description", props.get("Description", "")),
            "road":        props.get("roadway", props.get("road", props.get("Roadway", ""))),
            "direction":   props.get("direction", props.get("Direction", "")),
            "location":    props.get("location", props.get("Location", props.get("address", ""))),
            "county":      props.get("county", props.get("County", "")),
            "city":        props.get("city", props.get("City", "")),
            "severity":    props.get("severity", props.get("Severity", "")),
            "status":      props.get("status", props.get("Status", "Active")),
            "lat":         lat_val,
            "lon":         lon_val,
            "reported_at": props.get("reportedAt", props.get("start_time")),
            "source":      "fl511_json",
            "fetched_at":  datetime.now(timezone.utc),
            "processed":   False,
        }
        rec["_dedup_key"] = incident_id or hashlib.md5(
            (rec["road"] + rec["location"] + str(rec["reported_at"])).encode()
        ).hexdigest()
        records.append(rec)

    log.info("Parsed %d FL511 JSON incidents", len(records))
    return records


def _fetch_fl511_rss() -> list[dict]:
    """Fetch FL511 incidents from RSS feed (fallback)."""
    log.info("Fetching FL511 RSS: %s", FL511_RSS)
    try:
        resp = requests.get(FL511_RSS, timeout=TIMEOUT, verify=REQUESTS_CA)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log.error("FL511 RSS fetch error: %s", exc)
        return []

    records = []
    for item in root.findall(".//item"):
        def _t(tag):
            el = item.find(tag)
            return el.text.strip() if el is not None and el.text else ""

        title   = _t("title")
        desc    = _t("description")
        link    = _t("link")
        pubdate = _t("pubDate")

        dedup   = hashlib.md5((title + pubdate).encode()).hexdigest()
        rec = {
            "incident_id": dedup,
            "event_type":  title.split("-")[0].strip() if "-" in title else title,
            "description": desc,
            "road":        title.split("-")[1].strip() if "-" in title else "",
            "direction":   "",
            "location":    title,
            "county":      "",
            "city":        "",
            "severity":    "",
            "status":      "Active",
            "lat":         None,
            "lon":         None,
            "reported_at": pubdate,
            "link":        link,
            "source":      "fl511_rss",
            "fetched_at":  datetime.now(timezone.utc),
            "processed":   False,
            "_dedup_key":  dedup,
        }
        records.append(rec)

    log.info("Parsed %d FL511 RSS incidents", len(records))
    return records


def upsert_records(col, records: list[dict], county_filter: str | None = None,
                   dry_run: bool = False) -> int:
    if not records:
        return 0

    if county_filter:
        before = len(records)
        records = [r for r in records
                   if county_filter.lower() in (r.get("county") or "").lower()]
        log.info("County filter '%s': %d → %d records", county_filter, before, len(records))

    if dry_run:
        log.info("[dry-run] would upsert %d records", len(records))
        return 0

    ops = [
        UpdateOne(
            {"_dedup_key": r["_dedup_key"]},
            {"$setOnInsert": {"created_at": r["fetched_at"]},
             "$set": {k: v for k, v in r.items() if k != "created_at"}},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops, ordered=False)
    log.info("Upserted %d records (%d new inserts)", len(ops), result.upserted_count)
    return result.upserted_count


def run_once(county: str | None = None, dry_run: bool = False, verbose: bool = False) -> int:
    client = MongoClient(MONGO_URI)
    col    = client[DB_NAME][COLLECTION]

    records = _fetch_fl511_json()
    if not records:
        records = _fetch_fl511_rss()

    if verbose:
        for r in records[:5]:
            print(f"  [{r['source']}] {r.get('event_type')} — {r.get('location','')[:60]}")
        if len(records) > 5:
            print(f"  ... and {len(records)-5} more")

    written = upsert_records(col, records, county_filter=county, dry_run=dry_run)
    client.close()
    return written


def main():
    ap = argparse.ArgumentParser(description="Standalone FL511 traffic incident ETL")
    ap.add_argument("--loop",    action="store_true", help="Loop every POLL_INTERVAL seconds")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, no MongoDB writes")
    ap.add_argument("--verbose", action="store_true", help="Print sample parsed records")
    ap.add_argument("--county",  default=None,        help="Filter by county name (e.g. Alachua)")
    args = ap.parse_args()

    if args.loop:
        log.info("Starting loop — poll every %ds", POLL_INTERVAL)
        while True:
            try:
                run_once(county=args.county, dry_run=args.dry_run, verbose=args.verbose)
            except Exception as exc:
                log.error("Run error: %s", exc)
            time.sleep(POLL_INTERVAL)
    else:
        n = run_once(county=args.county, dry_run=args.dry_run, verbose=args.verbose)
        log.info("Done — %d records written", n)


if __name__ == "__main__":
    main()
