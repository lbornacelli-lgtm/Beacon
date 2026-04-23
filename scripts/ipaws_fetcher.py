#!/usr/bin/env python3
"""
scripts/ipaws_fetcher.py — Standalone IPAWS CAP Alert ETL

Fetches IPAWS/NWS CAP alerts from the FEMA open API, parses the XML,
and upserts records to MongoDB weather_rss.nws_alerts.

This is a manual/test ETL utility. The production service that runs
continuously is beacon-ipaws-fetcher (weather_rss/ipaws_fetcher.py).
Use this script for one-off backfills, debugging, or testing.

Usage:
    python3 scripts/ipaws_fetcher.py              # fetch once and exit
    python3 scripts/ipaws_fetcher.py --loop       # loop every POLL_INTERVAL seconds
    python3 scripts/ipaws_fetcher.py --dry-run    # parse only, no MongoDB writes
    python3 scripts/ipaws_fetcher.py --verbose    # show parsed records
    python3 scripts/ipaws_fetcher.py --state FL   # Florida only (default)
"""
import argparse
import hashlib
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import requests
from pymongo import MongoClient, UpdateOne

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ipaws_fetcher] %(levelname)s %(message)s",
)
log = logging.getLogger("ipaws_fetcher")

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URI      = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME        = "weather_rss"
COLLECTION     = "nws_alerts"
POLL_INTERVAL  = int(os.getenv("IPAWS_POLL_INTERVAL", "120"))  # seconds
REQUESTS_CA    = os.getenv("REQUESTS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt")
TIMEOUT        = 20   # seconds per HTTP request

# CAP / IPAWS feed base URL (Florida state polygon)
IPAWS_BASE  = "https://tdl.apps.fema.gov/IPAWSOPEN_EAS_SERVICE/rest/public/recent"
NWS_ATOM    = "https://api.weather.gov/alerts/active?area={state}&limit=500"

NS = {
    "cap":  "urn:oasis:names:tc:emergency:cap:1.2",
    "atom": "http://www.w3.org/2005/Atom",
}


def _fetch_nws_alerts(state: str = "FL") -> list[dict]:
    """Fetch active NWS/IPAWS alerts for a US state via api.weather.gov."""
    url = NWS_ATOM.format(state=state)
    log.info("Fetching NWS alerts: %s", url)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "FPREN/1.0 (lbornacelli-lgtm@github.com)"},
            timeout=TIMEOUT,
            verify=REQUESTS_CA,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.error("NWS fetch error: %s", exc)
        return []

    records = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        area  = props.get("areaDesc", "")

        rec = {
            "event":       props.get("event", ""),
            "headline":    props.get("headline", ""),
            "description": props.get("description", ""),
            "instruction": props.get("instruction", ""),
            "severity":    props.get("severity", ""),
            "urgency":     props.get("urgency", ""),
            "certainty":   props.get("certainty", ""),
            "status":      props.get("status", ""),
            "msgType":     props.get("messageType", ""),
            "category":    props.get("category", ""),
            "area_desc":   area,
            "sent":        props.get("sent"),
            "effective":   props.get("effective"),
            "onset":       props.get("onset"),
            "expires":     props.get("expires"),
            "ends":        props.get("ends"),
            "nws_id":      props.get("id", feat.get("id", "")),
            "source":      "nws_api",
            "fetched_at":  datetime.now(timezone.utc),
            "processed":   False,
        }

        # Stable dedup key
        raw_id = rec["nws_id"] or hashlib.md5(
            (rec["event"] + rec["area_desc"] + str(rec["sent"])).encode()
        ).hexdigest()
        rec["alert_id"] = raw_id
        records.append(rec)

    log.info("Parsed %d NWS alerts", len(records))
    return records


def _fetch_ipaws_cap(lookback_minutes: int = 5) -> list[dict]:
    """
    Fetch IPAWS CAP XML feed and parse to dicts.
    Falls back silently to empty list on SSL / network errors.
    """
    since = (datetime.utcnow() - timedelta(minutes=lookback_minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    url = f"{IPAWS_BASE}/{since}"
    log.info("Fetching IPAWS CAP: %s", url)
    try:
        resp = requests.get(url, timeout=TIMEOUT, verify=REQUESTS_CA)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        log.error("IPAWS CAP fetch error: %s", exc)
        return []

    records = []
    for alert_el in root.findall(".//cap:alert", NS):
        def _t(tag):
            el = alert_el.find(f"cap:{tag}", NS)
            return el.text.strip() if el is not None and el.text else ""

        info_el  = alert_el.find("cap:info", NS)
        event    = ""
        headline = ""
        description = ""
        instruction = ""
        if info_el is not None:
            event       = info_el.findtext("cap:event", default="", namespaces=NS)
            headline    = info_el.findtext("cap:headline", default="", namespaces=NS)
            description = info_el.findtext("cap:description", default="", namespaces=NS)
            instruction = info_el.findtext("cap:instruction", default="", namespaces=NS)

        identifier = _t("identifier")
        sent       = _t("sent")
        rec = {
            "event":       event.strip(),
            "headline":    headline.strip(),
            "description": description.strip(),
            "instruction": instruction.strip(),
            "severity":    "",
            "urgency":     "",
            "certainty":   "",
            "status":      _t("status"),
            "msgType":     _t("msgType"),
            "nws_id":      identifier,
            "alert_id":    identifier or hashlib.md5((event + sent).encode()).hexdigest(),
            "sent":        sent,
            "source":      "ipaws_cap",
            "fetched_at":  datetime.now(timezone.utc),
            "processed":   False,
        }

        if info_el is not None:
            for param in info_el.findall("cap:parameter", NS):
                vname = param.findtext("cap:valueName", default="", namespaces=NS)
                vval  = param.findtext("cap:value", default="", namespaces=NS)
                if vname.lower() == "severity":
                    rec["severity"] = vval
                elif vname.lower() == "urgency":
                    rec["urgency"] = vval
                elif vname.lower() == "certainty":
                    rec["certainty"] = vval

        records.append(rec)

    log.info("Parsed %d IPAWS CAP alerts", len(records))
    return records


def upsert_records(col, records: list[dict], dry_run: bool = False) -> int:
    """Upsert records by alert_id. Returns count written."""
    if not records:
        return 0
    if dry_run:
        log.info("[dry-run] would upsert %d records", len(records))
        return 0

    ops = [
        UpdateOne(
            {"alert_id": r["alert_id"]},
            {"$setOnInsert": {"created_at": r["fetched_at"]},
             "$set": {k: v for k, v in r.items() if k != "created_at"}},
            upsert=True,
        )
        for r in records
    ]
    result = col.bulk_write(ops, ordered=False)
    inserted = result.upserted_count
    log.info("Upserted %d / %d records (%d new inserts)", len(ops), len(ops), inserted)
    return inserted


def run_once(state: str = "FL", dry_run: bool = False, verbose: bool = False) -> int:
    client = MongoClient(MONGO_URI)
    col    = client[DB_NAME][COLLECTION]

    records = _fetch_nws_alerts(state)
    records += _fetch_ipaws_cap(lookback_minutes=10)

    if verbose:
        for r in records[:5]:
            print(f"  [{r['source']}] {r.get('event')} — {r.get('area_desc', '')[:60]}")
        if len(records) > 5:
            print(f"  ... and {len(records)-5} more")

    written = upsert_records(col, records, dry_run=dry_run)
    client.close()
    return written


def main():
    ap = argparse.ArgumentParser(description="Standalone IPAWS/NWS alert ETL")
    ap.add_argument("--loop",    action="store_true", help="Loop every POLL_INTERVAL seconds")
    ap.add_argument("--dry-run", action="store_true", help="Parse only, no MongoDB writes")
    ap.add_argument("--verbose", action="store_true", help="Print sample parsed records")
    ap.add_argument("--state",   default="FL",        help="US state code (default: FL)")
    args = ap.parse_args()

    if args.loop:
        log.info("Starting loop — poll every %ds", POLL_INTERVAL)
        while True:
            try:
                run_once(state=args.state, dry_run=args.dry_run, verbose=args.verbose)
            except Exception as exc:
                log.error("Run error: %s", exc)
            time.sleep(POLL_INTERVAL)
    else:
        n = run_once(state=args.state, dry_run=args.dry_run, verbose=args.verbose)
        log.info("Done — %d records written", n)


if __name__ == "__main__":
    main()
