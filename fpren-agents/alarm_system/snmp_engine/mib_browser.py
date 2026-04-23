"""
mib_browser.py — FPREN MIB File Browser & OID Store

Accepts MIB file uploads, parses OID trees using pysnmp's MIB compiler,
and stores the resulting OID → name + description mapping in MongoDB
mib_store collection.

Also provides lookup helpers used by the alarm dashboard to display
human-readable OID names in the alarm table.

CLI usage:
    python3 mib_browser.py --load /path/to/MY-MIB.txt
    python3 mib_browser.py --lookup .1.3.6.1.2.1.1.1.0
    python3 mib_browser.py --list

HTTP API (called from alarm_dashboard.py blueprint):
    POST /alarms/api/mibs/upload   — multipart/form-data, field: mib_file
    GET  /alarms/api/mibs          — list all loaded MIBs
    GET  /alarms/api/mibs/lookup?oid=.1.3.6.1.2.1.1.1.0
"""
import argparse
import logging
import os
import sys
import tempfile
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MIB_UPLOAD_DIR = "/home/ufuser/Fpren-main/fpren-agents/alarm_system/mibs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MIB-BROWSER] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

os.makedirs(MIB_UPLOAD_DIR, exist_ok=True)


def _get_db():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)["weather_rss"]


def _ensure_indexes(db):
    db["mib_store"].create_index([("oid", 1)], unique=True)
    db["mib_store"].create_index([("mib_name", 1)])


# ── MIB parsing ───────────────────────────────────────────────────────────────

def parse_mib_file(filepath: str) -> list[dict]:
    """
    Parse a MIB text file and return a list of OID records:
    [{"oid": ".1.3.6.1...", "name": "...", "syntax": "...", "description": "..."}]
    """
    try:
        from pysnmp.smi import builder as smi_builder, view as smi_view, compiler as smi_compiler
        from pysnmp.smi.rfc1902 import ObjectIdentity
    except ImportError:
        raise RuntimeError("pysnmp not installed — pip install pysnmp")

    results = []
    mib_dir = os.path.dirname(filepath)

    mib_builder = smi_builder.MibBuilder()
    mib_builder.setMibSources(
        smi_builder.DirMibSource(mib_dir),
        *mib_builder.getMibSources(),
    )
    smi_compiler.addMibCompiler(mib_builder, sources=[f"file://{mib_dir}"])
    mib_filename = os.path.splitext(os.path.basename(filepath))[0]

    try:
        mib_builder.loadModules(mib_filename)
    except Exception as exc:
        log.warning("MIB load error for %s: %s — continuing with partial parse.", filepath, exc)

    mib_view_controller = smi_view.MibViewController(mib_builder)

    # Walk all objects in the freshly loaded MIB
    for modName in mib_builder.mibSymbols:
        if modName != mib_filename:
            continue
        for symName, mibObj in mib_builder.mibSymbols[modName].items():
            try:
                node_name, module_name, oid = mib_view_controller.getNodeNameByOid(
                    mibObj.getName()
                )
                oid_str = "." + ".".join(str(x) for x in oid)
                desc_attr = getattr(mibObj, "description", None)
                description = desc_attr.prettyPrint() if desc_attr else ""
                syntax_attr = getattr(mibObj, "syntax", None)
                syntax = type(syntax_attr).__name__ if syntax_attr else ""
                results.append({
                    "oid":         oid_str,
                    "name":        symName,
                    "module":      module_name,
                    "syntax":      syntax,
                    "description": description[:500],
                })
            except Exception:
                pass

    log.info("Parsed %d OIDs from %s", len(results), mib_filename)
    return results


def load_mib(filepath: str, db=None) -> dict:
    """Parse a MIB file and upsert all OIDs into MongoDB mib_store."""
    if db is None:
        db = _get_db()
    _ensure_indexes(db)

    mib_name = os.path.splitext(os.path.basename(filepath))[0]
    records  = parse_mib_file(filepath)

    upserted  = 0
    errors    = 0
    now       = datetime.now(timezone.utc)

    for rec in records:
        try:
            db["mib_store"].update_one(
                {"oid": rec["oid"]},
                {"$set": {**rec, "mib_name": mib_name, "updated_at": now}},
                upsert=True,
            )
            upserted += 1
        except Exception as exc:
            log.error("mib_store upsert error: %s", exc)
            errors += 1

    # Record the MIB file metadata
    db["mib_files"].update_one(
        {"mib_name": mib_name},
        {"$set": {
            "mib_name":  mib_name,
            "filepath":  filepath,
            "oid_count": upserted,
            "loaded_at": now,
        }},
        upsert=True,
    )

    log.info("Loaded MIB %s — %d OIDs (%d errors)", mib_name, upserted, errors)
    return {"mib_name": mib_name, "oid_count": upserted, "errors": errors}


# ── OID lookup helpers ────────────────────────────────────────────────────────

def lookup_oid(oid: str, db=None) -> dict | None:
    """Return mib_store record for the given OID, or None."""
    if db is None:
        db = _get_db()
    return db["mib_store"].find_one({"oid": oid}, {"_id": 0})


def oid_display_name(oid: str, db=None) -> str:
    """Return 'MODULE::name' string for an OID, or the raw OID if not found."""
    rec = lookup_oid(oid, db)
    if rec:
        return f"{rec.get('module', '')}::{rec.get('name', oid)}"
    return oid


def list_mibs(db=None) -> list:
    """Return list of loaded MIB file metadata."""
    if db is None:
        db = _get_db()
    return list(db["mib_files"].find({}, {"_id": 0}).sort("mib_name", 1))


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(description="FPREN MIB Browser")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--load", metavar="FILE", help="Load a MIB file into MongoDB")
    grp.add_argument("--lookup", metavar="OID",  help="Look up an OID in mib_store")
    grp.add_argument("--list",   action="store_true", help="List all loaded MIBs")
    args = parser.parse_args()

    db = _get_db()

    if args.load:
        result = load_mib(args.load, db)
        print(f"Loaded: {result}")

    elif args.lookup:
        rec = lookup_oid(args.lookup, db)
        if rec:
            print(f"  OID:         {rec['oid']}")
            print(f"  Name:        {rec.get('name')}")
            print(f"  Module:      {rec.get('module')}")
            print(f"  Syntax:      {rec.get('syntax')}")
            print(f"  Description: {rec.get('description', '')[:200]}")
        else:
            print(f"OID {args.lookup!r} not found in mib_store.")

    elif args.list:
        mibs = list_mibs(db)
        if not mibs:
            print("No MIBs loaded.")
        for m in mibs:
            print(f"  {m['mib_name']:40s}  {m['oid_count']:5d} OIDs  "
                  f"loaded {m.get('loaded_at','?')}")


if __name__ == "__main__":
    _cli()
