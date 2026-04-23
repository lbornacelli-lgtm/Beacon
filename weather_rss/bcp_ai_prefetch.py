#!/usr/bin/env python3
"""
FPREN BCP AI Narrative Prefetch
---------------------------------
Pre-generates LiteLLM demographic narratives for all 67 FL counties and caches
them in MongoDB (collection: bcp_ai_narratives, TTL 7 days).

Run nightly so BCP PDF rendering never blocks on a live LiteLLM call.

Usage:
  python3 weather_rss/bcp_ai_prefetch.py                    # all 67 counties
  python3 weather_rss/bcp_ai_prefetch.py --county Alachua   # single county
  python3 weather_rss/bcp_ai_prefetch.py --dry-run          # print, no calls
"""
import sys, os, time, logging, argparse
sys.path.insert(0, "/home/ufuser/Fpren-main")

from pymongo import MongoClient, UpdateOne
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bcp_ai_prefetch")

MONGO_URI       = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
CACHE_TTL_HOURS = 7 * 24   # 7 days
RATE_LIMIT_SEC  = 1.0      # polite delay between LiteLLM calls

FL_COUNTIES = [
    "Alachua", "Baker", "Bay", "Bradford", "Brevard", "Broward",
    "Calhoun", "Charlotte", "Citrus", "Clay", "Collier", "Columbia",
    "Miami-Dade", "DeSoto", "Dixie", "Duval", "Escambia", "Flagler",
    "Franklin", "Gadsden", "Gilchrist", "Glades", "Gulf", "Hamilton",
    "Hardee", "Hendry", "Hernando", "Highlands", "Hillsborough", "Holmes",
    "Indian River", "Jackson", "Jefferson", "Lafayette", "Lake", "Lee",
    "Leon", "Levy", "Liberty", "Madison", "Manatee", "Marion",
    "Martin", "Monroe", "Nassau", "Okaloosa", "Okeechobee", "Orange",
    "Osceola", "Palm Beach", "Pasco", "Pinellas", "Polk", "Putnam",
    "Saint Johns", "Saint Lucie", "Santa Rosa", "Sarasota", "Seminole",
    "Sumter", "Suwannee", "Taylor", "Union", "Volusia", "Wakulla",
    "Walton", "Washington",
]


def run(counties: list, dry_run: bool = False) -> None:
    # Import after sys.path setup so the module resolves correctly
    from weather_rss.census_ai_analyzer import analyze_bcp_demographics

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db     = client["weather_rss"]
    col    = db["bcp_ai_narratives"]

    # TTL index on generated_at_unix (seconds since epoch)
    col.create_index("generated_at_unix",
                     expireAfterSeconds=int(CACHE_TTL_HOURS * 3600),
                     background=True)

    log.info("Prefetching BCP AI narratives for %d counties", len(counties))

    now     = datetime.now(timezone.utc)
    expires = now + timedelta(hours=CACHE_TTL_HOURS)
    ops     = []

    for i, county in enumerate(counties):
        log.info("[%d/%d] %s", i + 1, len(counties), county)

        if dry_run:
            log.info("  DRY RUN — skipping LiteLLM")
            continue

        narrative = analyze_bcp_demographics(county)
        log.info("  → %d chars", len(narrative))

        ops.append(UpdateOne(
            {"_id": county},
            {"$set": {
                "county":            county,
                "narrative":         narrative,
                "generated_at":      now.isoformat(),
                "generated_at_unix": now.timestamp(),
                "expires_at":        expires,
            }},
            upsert=True,
        ))

        if i < len(counties) - 1:
            time.sleep(RATE_LIMIT_SEC)

    if ops:
        result = col.bulk_write(ops, ordered=False)
        log.info("Cache updated: %d upserted, %d modified",
                 result.upserted_count, result.modified_count)

    client.close()
    log.info("BCP AI prefetch complete — %d counties", len(counties))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prefetch BCP AI demographic narratives")
    parser.add_argument("--county",  metavar="NAME",
                        help="Single county name (default: all 67)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print counties without calling LiteLLM")
    args = parser.parse_args()

    target_counties = [args.county] if args.county else FL_COUNTIES
    run(target_counties, dry_run=args.dry_run)
