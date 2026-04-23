#!/usr/bin/env python3
"""
Florida Rivers AI Agent — runs hourly, analyzes current river conditions
across Florida, and writes a broadcast-ready alert summary to MongoDB.

Uses the UF LiteLLM tool-calling agent (run_agent) with three tools:
  get_flood_conditions()         — gauges currently at/above Action stage
  get_gauge_trend(lid, hours)    — recent readings for a specific gauge
  get_active_flood_alerts()      — NWS flood/river alerts from nws_alerts

Writes to MongoDB collection: fl_river_alerts
  {
    alert_id:         uuid,
    generated_at:     datetime,
    severity:         "None"|"Action"|"Minor"|"Moderate"|"Major",
    flood_gauge_count: int,
    gauges_at_flood:  [{lid, name, river, category, stage_ft, trend}],
    summary_text:     "Broadcast-ready paragraph",
    broadcast_ready:  bool,
    ai_tool_calls:    int,
    fallback_used:    bool,
  }

Usage:
  python3 fl_rivers_agent.py         # single analysis run
  python3 fl_rivers_agent.py --loop  # run every RIVERS_AGENT_INTERVAL seconds
"""
import argparse
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient, DESCENDING

MONGO_URI            = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME              = "weather_rss"
AGENT_INTERVAL       = int(os.getenv("RIVERS_AGENT_INTERVAL", "3600"))  # 1 hour

LOG_FILE = os.getenv("LOG_FILE", "/home/ufuser/Fpren-main/weather_rss/logs/fl_rivers_agent.log")
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [RiversAgent] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("RiversAgent")

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    from weather_station.services.ai_client import run_agent, chat as ai_chat, is_configured as ai_ready
    from weather_station.config.ai_config import TOKENS_RIVER_RESPONSE
    _AI_AVAILABLE = True
except ImportError:
    TOKENS_RIVER_RESPONSE = 600
    _AI_AVAILABLE = False
    log.warning("ai_client not importable — will use rule-based fallback summaries")

FLOOD_SEVERITY_ORDER = {
    "Normal": 0, "Unknown": 0, "Action": 1,
    "Minor": 2, "Moderate": 3, "Major": 4, "Record": 5,
}

FLOOD_COLORS = {
    "Normal": "green", "Unknown": "gray", "Action": "yellow",
    "Minor": "orange", "Moderate": "red", "Major": "darkred", "Record": "purple",
}


# ── Tool functions ────────────────────────────────────────────────────────────

def _make_tools(db):
    """Return the (tool_schemas, tool_functions) pair for the agent."""

    def get_flood_conditions() -> dict:
        """
        Return all FL river gauges currently at or above Action stage.
        Includes gauge name, river, county, current stage, flood category, and trend.
        """
        docs = list(db.fl_river_gauges.find(
            {"flood_category": {"$in": ["Action", "Minor", "Moderate", "Major", "Record"]}},
            {"_id": 0, "lid": 1, "name": 1, "river": 1, "county": 1,
             "current_stage_ft": 1, "action_stage_ft": 1, "minor_stage_ft": 1,
             "moderate_stage_ft": 1, "major_stage_ft": 1, "flood_category": 1,
             "stage_trend": 1, "current_discharge_cfs": 1, "updated_at": 1},
        ))
        for d in docs:
            if isinstance(d.get("updated_at"), datetime):
                d["updated_at"] = d["updated_at"].isoformat()
        return {"flood_gauge_count": len(docs), "gauges": docs}

    def get_gauge_trend(lid: str, hours: int = 6) -> dict:
        """
        Return the last N hours of readings for a specific gauge (by lid).
        Useful for determining whether a gauge is rising or falling.
        """
        since = datetime.now(timezone.utc) - timedelta(hours=max(1, min(hours, 48)))
        docs = list(db.fl_river_readings.find(
            {"lid": lid, "fetched_at": {"$gte": since}},
            {"_id": 0, "gage_height_ft": 1, "discharge_cfs": 1,
             "flood_category": 1, "stage_trend": 1, "fetched_at": 1},
        ).sort("fetched_at", DESCENDING).limit(24))
        for d in docs:
            if isinstance(d.get("fetched_at"), datetime):
                d["fetched_at"] = d["fetched_at"].isoformat()
        if not docs:
            return {"lid": lid, "readings": [], "trend_note": "No recent data."}
        heights = [d["gage_height_ft"] for d in docs if d.get("gage_height_ft") is not None]
        if len(heights) >= 2:
            delta = heights[0] - heights[-1]  # newest - oldest
            direction = "rising" if delta > 0.1 else ("falling" if delta < -0.1 else "steady")
        else:
            direction = "unknown"
        return {
            "lid": lid,
            "readings": list(reversed(docs)),  # chronological
            "computed_trend": direction,
            "stage_change_ft": round(heights[0] - heights[-1], 2) if len(heights) >= 2 else None,
        }

    def get_active_flood_alerts() -> dict:
        """
        Return active NWS flood/river alerts from the nws_alerts collection.
        Includes flood warnings, watches, advisories, and river statements.
        """
        flood_keywords = [
            "Flood", "Flash Flood", "River", "Coastal Flood",
            "Hydrologic", "Areal Flood",
        ]
        query = {
            "$or": [{"event": {"$regex": kw, "$options": "i"}} for kw in flood_keywords]
        }
        docs = list(db.nws_alerts.find(
            query,
            {"_id": 0, "event": 1, "headline": 1, "area_desc": 1,
             "severity": 1, "sent": 1, "expires": 1},
        ).limit(20))
        return {"alert_count": len(docs), "alerts": docs}

    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "get_flood_conditions",
                "description": (
                    "Returns all Florida river gauges currently at or above Action stage, "
                    "including name, river, county, current stage in feet, flood category, "
                    "and trend direction."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_gauge_trend",
                "description": (
                    "Returns recent gauge readings for a specific gauge by lid "
                    "to determine if a river is rising or falling."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "lid":   {"type": "string", "description": "NWS location ID (e.g. JXVF1)"},
                        "hours": {"type": "integer", "description": "Hours of history (1–48)", "default": 6},
                    },
                    "required": ["lid"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_active_flood_alerts",
                "description": (
                    "Returns active NWS flood warnings, watches, and advisories "
                    "from the FPREN alert database."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]

    tool_functions = {
        "get_flood_conditions":   get_flood_conditions,
        "get_gauge_trend":        get_gauge_trend,
        "get_active_flood_alerts": get_active_flood_alerts,
    }

    return tool_schemas, tool_functions


# ── Fallback summary (no AI) ──────────────────────────────────────────────────

def _rule_based_summary(db) -> dict:
    """Generate a plain-text summary without LiteLLM."""
    flood_docs = list(db.fl_river_gauges.find(
        {"flood_category": {"$in": ["Action", "Minor", "Moderate", "Major", "Record"]}},
        {"_id": 0},
    ))

    if not flood_docs:
        normal_count = db.fl_river_gauges.count_documents({})
        return {
            "severity":        "None",
            "flood_gauge_count": 0,
            "gauges_at_flood": [],
            "summary_text": (
                f"Florida river conditions are normal. "
                f"All {normal_count} monitored gauges are below action stage. "
                f"No river flooding is currently reported."
            ),
            "broadcast_ready": True,
            "fallback_used":   True,
        }

    # Find worst category
    worst = max(flood_docs, key=lambda g: FLOOD_SEVERITY_ORDER.get(g.get("flood_category", "Normal"), 0))
    worst_cat = worst.get("flood_category", "Action")

    river_list = ", ".join(
        f"{g.get('river') or g.get('name')} ({g.get('flood_category')})"
        for g in flood_docs[:5]
    )
    more = f" and {len(flood_docs) - 5} others" if len(flood_docs) > 5 else ""

    summary = (
        f"Florida river flooding report: {len(flood_docs)} gauge(s) are currently at "
        f"or above action stage. Rivers affected include: {river_list}{more}. "
        f"The highest current flood category is {worst_cat} flooding. "
        f"Residents in low-lying areas near affected rivers should monitor conditions "
        f"and heed any official warnings."
    )

    return {
        "severity":          worst_cat,
        "flood_gauge_count": len(flood_docs),
        "gauges_at_flood": [
            {"lid": g.get("lid"), "name": g.get("name"), "river": g.get("river"),
             "flood_category": g.get("flood_category"), "county": g.get("county"),
             "current_stage_ft": g.get("current_stage_ft"),
             "stage_trend": g.get("stage_trend")}
            for g in flood_docs
        ],
        "summary_text":    summary,
        "broadcast_ready": True,
        "fallback_used":   True,
    }


# ── Agent run ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are FPREN's Florida Rivers Monitoring AI — part of the Florida Public
Radio Emergency Network. Your task is to analyze current river conditions across Florida and
produce a concise, broadcast-ready summary for radio audiences.

IMPORTANT: Use your tools FIRST to gather current data before writing any analysis.

Steps:
1. Call get_flood_conditions() to see which gauges are at or above Action stage.
2. If any gauges are flooding, call get_active_flood_alerts() to check for NWS warnings.
3. For the top 1–3 most severe gauges, call get_gauge_trend() to determine rising/falling.
4. Write a 2–4 sentence broadcast-ready summary. It should:
   - Lead with the flood situation (or "all clear" if nothing is flooding)
   - Name the specific rivers and counties affected
   - Note whether conditions are rising or falling
   - Advise residents of any immediate safety concerns
   - Be suitable for direct on-air reading on FPREN

Also output a JSON block (after your main summary) in this exact format:
{"severity": "None|Action|Minor|Moderate|Major", "flood_gauge_count": N}

Keep your entire response under 400 words."""


def run_agent_analysis(db) -> dict:
    """Run the LiteLLM agent and return a structured result dict."""
    if not _AI_AVAILABLE or not ai_ready():
        log.info("AI not available — using rule-based fallback")
        return _rule_based_summary(db)

    tool_schemas, tool_functions = _make_tools(db)

    log.info("Starting LiteLLM agent analysis...")
    try:
        result = run_agent(
            system_prompt=SYSTEM_PROMPT,
            tools=tool_schemas,
            tool_functions=tool_functions,
            initial_message=(
                "Please analyze current Florida river conditions and produce "
                "a broadcast-ready summary. Use your tools to gather current data."
            ),
            max_iterations=8,
            max_tokens=TOKENS_RIVER_RESPONSE,
        )
    except Exception as exc:
        log.error("Agent error: %s — falling back to rule-based", exc)
        return _rule_based_summary(db)

    raw_response  = result.get("response", "")
    tool_calls    = result.get("tool_calls", [])
    iterations    = result.get("iterations", 0)

    log.info("Agent finished: %d tool calls, %d iterations", len(tool_calls), iterations)

    # Extract JSON metadata from response if present
    severity        = "None"
    flood_gauge_count = 0
    try:
        import re
        m = re.search(r'\{[^}]*"severity"[^}]*\}', raw_response, re.DOTALL)
        if m:
            meta = json.loads(m.group())
            severity          = meta.get("severity", "None")
            flood_gauge_count = int(meta.get("flood_gauge_count", 0))
            # Strip JSON block from broadcast text
            raw_response = raw_response[:m.start()].strip()
    except Exception:
        pass

    # Collect flood gauge list from tool call results
    gauges_at_flood = []
    for tc in tool_calls:
        if tc.get("tool") == "get_flood_conditions":
            gauges_at_flood = tc.get("result", {}).get("gauges", [])
            break

    return {
        "severity":          severity,
        "flood_gauge_count": flood_gauge_count or len(gauges_at_flood),
        "gauges_at_flood":   gauges_at_flood,
        "summary_text":      raw_response,
        "broadcast_ready":   True,
        "ai_tool_calls":     len(tool_calls),
        "ai_iterations":     iterations,
        "fallback_used":     False,
    }


# ── Persistence ───────────────────────────────────────────────────────────────

def save_alert(db, analysis: dict):
    """Store the analysis result in fl_river_alerts and return the document."""
    doc = {
        "alert_id":          str(uuid.uuid4()),
        "generated_at":      datetime.now(timezone.utc),
        **analysis,
    }
    db.fl_river_alerts.insert_one(doc)
    db.fl_river_alerts.create_index(
        [("generated_at", DESCENDING)],
        name="generated_at_desc",
    )
    # Keep only last 720 alerts (~30 days at hourly)
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    db.fl_river_alerts.delete_many({"generated_at": {"$lt": cutoff}})
    log.info(
        "Saved alert: severity=%s, gauges_at_flood=%d, broadcast_ready=%s",
        doc["severity"], doc.get("flood_gauge_count", 0), doc["broadcast_ready"],
    )
    return doc


# ── Main ─────────────────────────────────────────────────────────────────────

def run_once(db):
    analysis = run_agent_analysis(db)
    return save_alert(db, analysis)


def main():
    parser = argparse.ArgumentParser(description="FPREN Florida Rivers AI Agent")
    parser.add_argument("--loop", action="store_true",
                        help="Run continuously every RIVERS_AGENT_INTERVAL seconds")
    args = parser.parse_args()

    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]

    if args.loop:
        log.info("Starting continuous loop (interval=%ds)", AGENT_INTERVAL)
        while True:
            try:
                run_once(db)
            except Exception as exc:
                log.error("Unhandled error: %s", exc, exc_info=True)
            time.sleep(AGENT_INTERVAL)
    else:
        doc = run_once(db)
        print("\n─── FPREN River Alert ───")
        print(f"Severity:  {doc['severity']}")
        print(f"At Flood:  {doc.get('flood_gauge_count', 0)} gauges")
        print(f"\nSummary:\n{doc['summary_text']}")


if __name__ == "__main__":
    main()
