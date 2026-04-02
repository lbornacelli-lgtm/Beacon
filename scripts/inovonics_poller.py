#!/usr/bin/env python3
"""
Inovonics 677 EAS LP-1 SNMP Poller
===================================
Polls the Inovonics 677 EAS receiver at a configured IP address via SNMP,
stores the latest status in MongoDB collection 'eas_monitor'.

Called on-demand from the Shiny dashboard (Poll Now button) and can also
be scheduled via systemd timer for background polling.

Inovonics enterprise OID: 1.3.6.1.4.1.13867
Device: Inovonics EN677 EAS LP-1 Monitoring Receiver (3 source inputs)

Run:
    python3 scripts/inovonics_poller.py
    python3 scripts/inovonics_poller.py --host 10.245.74.39 --community public
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
from pymongo import MongoClient

# ── Inovonics 677 OID Map ─────────────────────────────────────────────────────
# Enterprise base: 1.3.6.1.4.1.13867
# These OIDs follow the Inovonics BARON MIB for the EN677 product line.
# Verify against the actual .mib file from Inovonics if values show as Unknown.

INOVONICS_ENTERPRISE = "1.3.6.1.4.1.13867"

# Standard system OIDs (RFC 1213 / SNMPv2-MIB)
SYS_OIDS = {
    "1.3.6.1.2.1.1.1.0": "sysDescr",
    "1.3.6.1.2.1.1.3.0": "sysUpTime",
    "1.3.6.1.2.1.1.5.0": "sysName",
    "1.3.6.1.2.1.1.6.0": "sysLocation",
}

# Inovonics 677 — per-source OIDs (replace {n} with 1, 2, or 3)
SOURCE_OID_TEMPLATES = {
    "label":        f"{INOVONICS_ENTERPRISE}.4.2.1.2.{{n}}",  # Station label/name
    "frequency":    f"{INOVONICS_ENTERPRISE}.4.2.1.3.{{n}}",  # Tuned frequency (kHz × 10)
    "rssi":         f"{INOVONICS_ENTERPRISE}.4.2.1.4.{{n}}",  # Signal strength (dBuV)
    "stereo":       f"{INOVONICS_ENTERPRISE}.4.2.1.5.{{n}}",  # Stereo pilot (0=mono,1=stereo)
    "audio":        f"{INOVONICS_ENTERPRISE}.4.2.1.6.{{n}}",  # Audio presence (0=silent,1=present)
    "rds":          f"{INOVONICS_ENTERPRISE}.4.2.1.7.{{n}}",  # RDS data (0=none,1=present)
    "eas_active":   f"{INOVONICS_ENTERPRISE}.4.2.1.8.{{n}}",  # EAS alert active (0=normal,1=alert)
    "eas_msg":      f"{INOVONICS_ENTERPRISE}.4.2.1.9.{{n}}",  # Last EAS message text
}

# Global device-level OIDs
DEVICE_OIDS = {
    "model":        f"{INOVONICS_ENTERPRISE}.4.1.1.0",
    "firmware":     f"{INOVONICS_ENTERPRISE}.4.1.2.0",
    "serial":       f"{INOVONICS_ENTERPRISE}.4.1.3.0",
    "alert_count":  f"{INOVONICS_ENTERPRISE}.4.1.4.0",  # Total EAS alerts received
}


def snmp_get(host: str, community: str, oid: str, version: str = "2c") -> str | None:
    """Single OID get via snmpget. Returns string value or None on failure."""
    try:
        result = subprocess.run(
            ["snmpget", f"-v{version}", "-c", community, "-Oqv", host, oid],
            capture_output=True, text=True, timeout=5
        )
        val = result.stdout.strip()
        if result.returncode != 0 or not val or "No Such" in val or "Timeout" in val:
            return None
        # Strip surrounding quotes if present
        return val.strip('"')
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def snmp_walk(host: str, community: str, base_oid: str, version: str = "2c") -> dict:
    """Walk an OID subtree. Returns {oid: value} dict."""
    results = {}
    try:
        result = subprocess.run(
            ["snmpwalk", f"-v{version}", "-c", community, "-Oqn", host, base_oid],
            capture_output=True, text=True, timeout=15
        )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            parts = line.split("=", 1)
            if len(parts) == 2:
                oid_part = parts[0].strip()
                val_part = parts[1].strip().strip('"')
                results[oid_part] = val_part
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return results


def format_frequency(raw_val: str | None) -> str:
    """Convert raw frequency value to MHz string. e.g. '1023' → '102.3 MHz'"""
    if raw_val is None:
        return "Unknown"
    try:
        khz10 = int(raw_val)
        mhz = khz10 / 10.0
        return f"{mhz:.1f} MHz"
    except (ValueError, TypeError):
        return raw_val


def decode_bool(raw_val: str | None, true_str: str, false_str: str) -> str:
    if raw_val is None:
        return "Unknown"
    return true_str if raw_val.strip() in ("1", "true", "True") else false_str


def poll(host: str, community: str, version: str = "2c") -> dict:
    """Full poll of the Inovonics 677. Returns a result document for MongoDB."""
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    reachable = False
    device_info = {}
    sources = []

    # ── Test reachability via sysDescr ───────────────────────────────────────
    sys_descr = snmp_get(host, community, "1.3.6.1.2.1.1.1.0", version)
    if sys_descr is not None:
        reachable = True
        device_info["sysDescr"]   = sys_descr
        device_info["sysUpTime"]  = snmp_get(host, community, "1.3.6.1.2.1.1.3.0", version) or ""
        device_info["sysName"]    = snmp_get(host, community, "1.3.6.1.2.1.1.5.0", version) or ""
        device_info["sysLocation"]= snmp_get(host, community, "1.3.6.1.2.1.1.6.0", version) or ""

    if reachable:
        # ── Device-level Inovonics OIDs ───────────────────────────────────────
        for key, oid in DEVICE_OIDS.items():
            val = snmp_get(host, community, oid, version)
            if val:
                device_info[key] = val

        # ── Per-source data (3 sources) ───────────────────────────────────────
        for n in range(1, 4):
            src = {"source_num": n}
            for field, template in SOURCE_OID_TEMPLATES.items():
                oid = template.format(n=n)
                raw = snmp_get(host, community, oid, version)
                src[f"raw_{field}"] = raw
            # Friendly values
            src["label"]      = src.get("raw_label") or f"Station {n}"
            src["frequency"]  = format_frequency(src.get("raw_frequency"))
            src["rssi_dbm"]   = f"{src['raw_rssi']} dBμV" if src.get("raw_rssi") else "Unknown"
            src["stereo"]     = decode_bool(src.get("raw_stereo"),    "Stereo", "Mono")
            src["audio"]      = decode_bool(src.get("raw_audio"),     "Present", "Silent")
            src["rds"]        = decode_bool(src.get("raw_rds"),       "Present", "None")
            src["eas_active"] = decode_bool(src.get("raw_eas_active"),"ALERT", "Normal")
            src["eas_msg"]    = src.get("raw_eas_msg") or ""
            sources.append(src)

        # ── Raw OID walk for admin inspection ─────────────────────────────────
        raw_walk = snmp_walk(host, community, INOVONICS_ENTERPRISE, version)
    else:
        raw_walk = {}
        # Return placeholder sources with Unknown status when device unreachable
        for n in range(1, 4):
            sources.append({
                "source_num": n,
                "label":      f"Station {n}",
                "frequency":  "Unknown",
                "rssi_dbm":   "Unknown",
                "stereo":     "Unknown",
                "audio":      "Unknown",
                "rds":        "Unknown",
                "eas_active": "Unknown",
                "eas_msg":    "",
            })

    return {
        "_id":         "singleton",
        "host":        host,
        "community":   community,
        "reachable":   reachable,
        "polled_at":   now_utc,
        "device_info": device_info,
        "sources":     sources,
        "raw_oid_walk": raw_walk,
    }


def main():
    parser = argparse.ArgumentParser(description="Poll Inovonics 677 via SNMP")
    parser.add_argument("--host",      default="10.245.74.39", help="Device IP")
    parser.add_argument("--community", default="public",       help="SNMP community string")
    parser.add_argument("--version",   default="2c",           help="SNMP version (1, 2c)")
    parser.add_argument("--mongo",     default=os.getenv("MONGO_URI", "mongodb://localhost:27017/"),
                        help="MongoDB URI")
    args = parser.parse_args()

    print(f"Polling Inovonics 677 at {args.host} (community={args.community}, v{args.version})...")
    doc = poll(args.host, args.community, args.version)

    print(f"  Reachable: {doc['reachable']}")
    if doc["reachable"]:
        print(f"  Device:    {doc['device_info'].get('sysDescr','')}")
        for src in doc["sources"]:
            eas_flag = " *** EAS ALERT ***" if src["eas_active"] == "ALERT" else ""
            print(f"  Source {src['source_num']}: {src['label']} {src['frequency']} "
                  f"RSSI={src['rssi_dbm']} {src['stereo']} Audio={src['audio']}{eas_flag}")

    # Store in MongoDB (upsert on singleton _id)
    try:
        client = MongoClient(args.mongo)
        col = client["weather_rss"]["eas_monitor"]
        col.replace_one({"_id": "singleton"}, doc, upsert=True)
        client.close()
        print(f"  Stored to MongoDB eas_monitor collection.")
    except Exception as e:
        print(f"  MongoDB error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
