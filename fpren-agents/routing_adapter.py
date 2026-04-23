"""
routing_adapter.py — FPREN LiteLLM Routing Adapter

Reads 24-hour performance data from weather_rss.execution_log, applies
demotion rules when a model tier exceeds latency / retry / error thresholds,
and writes the resulting MODEL_MAP overrides to routing_config.json.

litellm_router.py reads routing_config.json at import time so the next
director restart (or explicit reload_routing() call) picks up the changes.

Run standalone:   python3 routing_adapter.py
Called from:      Director.__init__() at startup
"""
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from pymongo import MongoClient

log = logging.getLogger("RoutingAdapter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [ADAPTER] %(message)s")

MONGO_URI   = "mongodb://localhost:27017"
CONFIG_PATH = Path(__file__).parent / "routing_config.json"

# Mirrors the defaults in litellm_router.py
BASELINE_MAP = {
    "small":  "openai/llama-3.1-8b-instruct",
    "medium": "openai/llama-3.3-70b-instruct",
    "large":  "openai/nemotron-3-super-120b-a12b",
}

# Demotion triggers
LATENCY_THRESHOLD_MS = 25_000   # p50 > 25 s  → demote
RETRY_RATE_THRESHOLD = 0.20     # > 20% retried → demote
ERROR_RATE_THRESHOLD = 0.15     # > 15% errors  → demote
MIN_SAMPLE_SIZE      = 10       # ignore tiers with fewer samples

DEMOTION_MAP = {
    "large":  "medium",
    "medium": "small",
    "small":  "small",   # floor
}


def load_tier_stats(lookback_hours=24):
    """
    Aggregate execution_log over the last `lookback_hours` grouped by
    model_tier.  Returns a list of dicts with keys:
      _id, n, avg_latency, retry_rate, error_rate, avg_score
    """
    client  = MongoClient(MONGO_URI)
    cutoff  = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    pipeline = [
        {"$match": {"timestamp": {"$gte": cutoff}}},
        {"$group": {
            "_id":         "$model_tier",
            "n":           {"$sum": 1},
            "avg_latency": {"$avg": "$latency_ms"},
            "retry_rate":  {"$avg": {"$cond": [{"$gt": ["$retry_count", 0]}, 1, 0]}},
            "error_rate":  {"$avg": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
            "avg_score":   {"$avg": "$score"},
        }},
    ]
    try:
        return list(client["weather_rss"]["execution_log"].aggregate(pipeline))
    except Exception as exc:
        log.error("Failed to read execution_log: %s", exc)
        return []
    finally:
        client.close()


def compute_overrides(tier_stats):
    """
    Returns MODEL_MAP dict (tier → model_id) with demotions applied where
    any threshold is breached.  Tiers with < MIN_SAMPLE_SIZE calls keep
    the baseline assignment.
    """
    overrides    = dict(BASELINE_MAP)
    demotion_log = []
    stat_map     = {r["_id"]: r for r in tier_stats if r.get("_id")}

    for tier in ("large", "medium", "small"):
        row = stat_map.get(tier)
        if not row or (row.get("n") or 0) < MIN_SAMPLE_SIZE:
            continue

        reasons = []
        if (row.get("avg_latency") or 0) > LATENCY_THRESHOLD_MS:
            reasons.append(f"avg_latency={row['avg_latency']:.0f}ms")
        if (row.get("retry_rate") or 0) > RETRY_RATE_THRESHOLD:
            reasons.append(f"retry_rate={row['retry_rate']:.1%}")
        if (row.get("error_rate") or 0) > ERROR_RATE_THRESHOLD:
            reasons.append(f"error_rate={row['error_rate']:.1%}")

        if reasons:
            demoted_tier    = DEMOTION_MAP[tier]
            overrides[tier] = BASELINE_MAP[demoted_tier]
            demotion_log.append(
                f"  {tier} → {demoted_tier}  ({', '.join(reasons)})"
            )

    if demotion_log:
        log.info("Routing adjustments applied:\n%s", "\n".join(demotion_log))
    else:
        log.info("All tiers within thresholds — baseline routing unchanged")

    return overrides


def write_config(overrides, tier_stats):
    """Write routing_config.json next to litellm_router.py."""
    config = {
        "model_map":      overrides,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "lookback_hours": 24,
        "tier_stats": {
            r["_id"]: {
                "n":           r.get("n"),
                "avg_latency": round(r.get("avg_latency") or 0, 1),
                "retry_rate":  round(r.get("retry_rate")  or 0, 4),
                "error_rate":  round(r.get("error_rate")  or 0, 4),
                "avg_score":   round(r.get("avg_score")   or 0, 4),
            }
            for r in tier_stats if r.get("_id")
        },
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    log.info("Wrote %s", CONFIG_PATH.name)


def run():
    """Load stats, compute overrides, write config.  Returns the override map."""
    log.info("Computing routing overrides from last 24 h of execution_log")
    stats     = load_tier_stats(lookback_hours=24)
    overrides = compute_overrides(stats)
    write_config(overrides, stats)
    return overrides


if __name__ == "__main__":
    run()
