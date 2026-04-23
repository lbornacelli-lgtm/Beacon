#!/usr/bin/env python3
"""
FPREN Emergency Resources Prefetch
------------------------------------
Queries OpenStreetMap Overpass API for hospitals, fire stations, and police
within 15 km of every FL county centroid + every registered user asset, then
caches the results in MongoDB (collection: emergency_resources_cache, TTL 72 h).

Run nightly so BCP PDF rendering never blocks on a live Overpass HTTP call.

Usage:
  python3 weather_rss/emergency_resources_prefetch.py           # all locations
  python3 weather_rss/emergency_resources_prefetch.py --dry-run # print, no calls
"""
import sys, os, time, math, logging, argparse
sys.path.insert(0, "/home/ufuser/Fpren-main")

import requests
from pymongo import MongoClient, UpdateOne
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("emergency_prefetch")

MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
CACHE_TTL_HOURS  = 72
RADIUS_M         = 15000
OVERPASS_URL     = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 25
RATE_LIMIT_SEC   = 2.5   # polite delay between Overpass calls
LAT_ROUND        = 2     # cache key precision (~1.1 km grid)

# All 67 FL county centroids (mirrored from app.R FL_COUNTY_LATLON)
FL_COUNTY_CENTROIDS = [
    ("Alachua", 29.67, -82.49),    ("Baker", 30.33, -82.27),
    ("Bay", 30.21, -85.62),        ("Bradford", 29.94, -82.14),
    ("Brevard", 28.23, -80.72),    ("Broward", 26.15, -80.46),
    ("Calhoun", 30.41, -85.16),    ("Charlotte", 26.97, -81.94),
    ("Citrus", 28.84, -82.47),     ("Clay", 29.98, -81.76),
    ("Collier", 26.11, -81.39),    ("Columbia", 30.22, -82.62),
    ("Miami-Dade", 25.55, -80.60), ("DeSoto", 27.19, -81.83),
    ("Dixie", 29.68, -83.18),      ("Duval", 30.33, -81.66),
    ("Escambia", 30.55, -87.35),   ("Flagler", 29.47, -81.21),
    ("Franklin", 29.80, -84.89),   ("Gadsden", 30.59, -84.63),
    ("Gilchrist", 29.72, -82.74),  ("Glades", 26.94, -81.11),
    ("Gulf", 29.87, -85.23),       ("Hamilton", 30.48, -82.97),
    ("Hardee", 27.49, -81.83),     ("Hendry", 26.50, -80.91),
    ("Hernando", 28.55, -82.41),   ("Highlands", 27.35, -81.28),
    ("Hillsborough", 27.90, -82.33), ("Holmes", 30.87, -85.81),
    ("Indian River", 27.74, -80.57), ("Jackson", 30.83, -85.22),
    ("Jefferson", 30.52, -83.81),  ("Lafayette", 30.07, -83.16),
    ("Lake", 28.76, -81.76),       ("Lee", 26.56, -81.71),
    ("Leon", 30.46, -84.28),       ("Levy", 29.31, -82.83),
    ("Liberty", 30.25, -84.88),    ("Madison", 30.46, -83.42),
    ("Manatee", 27.48, -82.57),    ("Marion", 29.23, -82.07),
    ("Martin", 27.09, -80.41),     ("Monroe", 24.70, -81.52),
    ("Nassau", 30.52, -81.60),     ("Okaloosa", 30.74, -86.49),
    ("Okeechobee", 27.24, -80.88), ("Orange", 28.45, -81.32),
    ("Osceola", 28.06, -81.26),    ("Palm Beach", 26.65, -80.25),
    ("Pasco", 28.28, -82.40),      ("Pinellas", 27.86, -82.77),
    ("Polk", 27.90, -81.70),       ("Putnam", 29.56, -81.68),
    ("Saint Johns", 29.96, -81.41), ("Saint Lucie", 27.35, -80.40),
    ("Santa Rosa", 30.76, -86.92), ("Sarasota", 27.22, -82.48),
    ("Seminole", 28.66, -81.26),   ("Sumter", 28.69, -82.07),
    ("Suwannee", 30.18, -83.14),   ("Taylor", 30.04, -83.61),
    ("Union", 29.98, -82.39),      ("Volusia", 29.07, -81.24),
    ("Wakulla", 30.26, -84.39),    ("Walton", 30.62, -86.17),
    ("Washington", 30.63, -85.67),
]


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, LAT_ROUND):.2f}_{round(lon, LAT_ROUND):.2f}"


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


def fetch_overpass(lat: float, lon: float) -> list:
    query = (
        f"[out:json][timeout:{OVERPASS_TIMEOUT}];"
        f"(node[\"amenity\"~\"hospital|fire_station|police\"]"
        f"(around:{RADIUS_M},{lat},{lon});"
        f"way[\"amenity\"~\"hospital|fire_station|police\"]"
        f"(around:{RADIUS_M},{lat},{lon}););"
        f"out body center 8;"
    )
    try:
        r = requests.post(
            OVERPASS_URL, data={"data": query}, timeout=OVERPASS_TIMEOUT + 5
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as e:
        log.warning("Overpass error at (%.4f, %.4f): %s", lat, lon, e)
        return []

    type_map = {
        "hospital": "Hospital",
        "fire_station": "Fire Station",
        "police": "Police",
    }
    rows = []
    for el in elements:
        tags  = el.get("tags", {})
        lat_e = el.get("lat") or (el.get("center") or {}).get("lat")
        lon_e = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat_e is None or lon_e is None:
            continue
        amenity = tags.get("amenity", "")
        addr_parts = []
        hn = tags.get("addr:housenumber")
        st = tags.get("addr:street")
        if hn and st:
            addr_parts.append(f"{hn} {st}")
        for k in ("addr:city", "addr:state", "addr:postcode"):
            if tags.get(k):
                addr_parts.append(tags[k])
        rows.append({
            "Type":    type_map.get(amenity, amenity),
            "Name":    tags.get("name", "(unnamed)"),
            "Address": ", ".join(addr_parts) if addr_parts else "Address not listed",
            "Dist_km": _haversine_km(lat, lon, float(lat_e), float(lon_e)),
        })

    rows.sort(key=lambda x: x["Dist_km"])
    by_type: dict = {}
    for row in rows:
        by_type.setdefault(row["Type"], []).append(row)
    result = []
    for t_rows in by_type.values():
        result.extend(t_rows[:2])   # top 2 per type
    return result


def collect_locations(db) -> list:
    """County centroids + all user asset coords, deduplicated by cache key."""
    seen: set = set()
    locs: list = []

    for name, lat, lon in FL_COUNTY_CENTROIDS:
        k = _cache_key(lat, lon)
        if k not in seen:
            seen.add(k)
            locs.append((lat, lon, f"county:{name}"))

    try:
        users = list(db["users"].find(
            {"assets": {"$exists": True, "$not": {"$size": 0}}},
            {"username": 1, "assets": 1, "_id": 0},
        ))
        for u in users:
            for a in (u.get("assets") or []):
                try:
                    lat = float(a.get("lat") or 0)
                    lon = float(a.get("lon") or 0)
                except (TypeError, ValueError):
                    continue
                if not lat or not lon:
                    continue
                k = _cache_key(lat, lon)
                if k not in seen:
                    seen.add(k)
                    locs.append((lat, lon, f"asset:{a.get('asset_name','unknown')}"))
    except Exception as e:
        log.warning("Could not load user assets from MongoDB: %s", e)

    return locs


def run(dry_run: bool = False) -> None:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db     = client["weather_rss"]
    col    = db["emergency_resources_cache"]

    # TTL index on fetched_at_unix (seconds since epoch)
    col.create_index("fetched_at_unix",
                     expireAfterSeconds=int(CACHE_TTL_HOURS * 3600),
                     background=True)

    locs = collect_locations(db)
    log.info("Prefetching emergency resources for %d unique locations", len(locs))

    now     = datetime.now(timezone.utc)
    expires = now + timedelta(hours=CACHE_TTL_HOURS)
    ops     = []

    for i, (lat, lon, label) in enumerate(locs):
        key = _cache_key(lat, lon)
        log.info("[%d/%d] %s  key=%s", i + 1, len(locs), label, key)

        if dry_run:
            log.info("  DRY RUN — skipping Overpass")
            continue

        resources = fetch_overpass(lat, lon)
        log.info("  → %d resources cached", len(resources))

        ops.append(UpdateOne(
            {"_id": key},
            {"$set": {
                "lat":             lat,
                "lon":             lon,
                "label":           label,
                "resources":       resources,
                "fetched_at":      now.isoformat(),
                "fetched_at_unix": now.timestamp(),
                "expires_at":      expires,
            }},
            upsert=True,
        ))

        if i < len(locs) - 1:
            time.sleep(RATE_LIMIT_SEC)

    if ops:
        result = col.bulk_write(ops, ordered=False)
        log.info("Cache updated: %d upserted, %d modified",
                 result.upserted_count, result.modified_count)

    client.close()
    log.info("Emergency resources prefetch complete — %d locations", len(locs))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prefetch Overpass emergency resources")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print locations without calling Overpass")
    run(dry_run=parser.parse_args().dry_run)
