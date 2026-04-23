"""
alarm_rules.py — FPREN Alarm Rule Definitions
Stores and retrieves user-defined OID threshold rules,
feed staleness thresholds, and notification routing rules in MongoDB.
All rules live in weather_rss.alarm_rules collection.
"""
import logging
import os
from datetime import datetime, timezone

from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
log = logging.getLogger(__name__)

# ── Default feed-staleness rules ─────────────────────────────────────────────
DEFAULT_FEED_RULES = [
    {
        "rule_id": "feed_nws_alerts",
        "source": "feed_watchdog",
        "name": "NWS Alerts Feed Stale",
        "collection": "nws_alerts",
        "field": "fetched_at",
        "check_type": "staleness",
        "warn_minutes": 120,
        "minor_minutes": 360,
        "major_minutes": 720,
        "critical_minutes": 1440,
        # DISABLED: nws_alerts only receives new documents when FEMA issues active FL
        # alerts. During calm weather the collection goes days without updates even
        # though the fetcher is healthy. Staleness checks against this collection
        # produce constant false alarms. Monitor the service via systemd instead.
        "enabled": False,
        "remediation": (
            "sudo systemctl status beacon-ipaws-fetcher\n"
            "sudo journalctl -u beacon-ipaws-fetcher -n 30"
        ),
    },
    {
        "rule_id": "feed_fl_traffic",
        "source": "feed_watchdog",
        "name": "FL511 Traffic Feed Stale",
        "collection": "fl_traffic",
        "field": "fetched_at",
        "check_type": "staleness",
        "warn_minutes": 35,
        "minor_minutes": 60,
        "major_minutes": 90,
        "critical_minutes": 120,
        "enabled": True,
        "remediation": (
            "sudo systemctl status beacon-extended-fetcher\n"
            "sudo journalctl -u beacon-extended-fetcher -n 30"
        ),
    },
    {
        "rule_id": "feed_waze_alerts",
        "source": "feed_watchdog",
        "name": "Waze Alerts Feed Stale",
        "collection": "waze_alerts",
        "field": "fetched_at",
        "check_type": "staleness",
        "warn_minutes": 5,
        "minor_minutes": 10,
        "major_minutes": 20,
        "critical_minutes": 30,
        # DISABLED: beacon-waze-fetcher is intentionally not running — no Waze CCP
        # feed URL is configured. Enable this rule only after setting WAZE_FEED_URL.
        "enabled": False,
        "remediation": "sudo systemctl status beacon-waze-fetcher",
    },
    {
        "rule_id": "feed_waze_jams",
        "source": "feed_watchdog",
        "name": "Waze Jams Feed Stale",
        "collection": "waze_jams",
        "field": "fetched_at",
        "check_type": "staleness",
        "warn_minutes": 5,
        "minor_minutes": 10,
        "major_minutes": 20,
        "critical_minutes": 30,
        # DISABLED: same reason as feed_waze_alerts — no Waze CCP feed URL configured.
        "enabled": False,
        "remediation": "sudo systemctl status beacon-waze-fetcher",
    },
    {
        "rule_id": "feed_airport_metar",
        "source": "feed_watchdog",
        "name": "Airport METAR Feed Stale",
        "collection": "airport_metar",
        "field": "fetched_at",
        "check_type": "staleness",
        "warn_minutes": 20,
        "minor_minutes": 30,
        "major_minutes": 45,
        "critical_minutes": 60,
        "enabled": True,
        "remediation": "sudo systemctl status beacon-obs-fetcher",
    },
    {
        "rule_id": "feed_river_gauges",
        "source": "feed_watchdog",
        "name": "River Gauge Feed Stale",
        "collection": "fl_river_gauges",
        # fl_river_gauges uses updated_at (set by fl_rivers_fetcher), not fetched_at
        "field": "updated_at",
        "check_type": "staleness",
        "warn_minutes": 30,
        "minor_minutes": 60,
        "major_minutes": 120,
        "critical_minutes": 240,
        "enabled": True,
        "remediation": "sudo systemctl status beacon-rivers-fetcher",
    },
    {
        "rule_id": "feed_tts_queue_backlog",
        "source": "feed_watchdog",
        "name": "TTS Queue Backlog",
        "collection": "tts_queue",
        "field": "created_at",
        "check_type": "backlog",
        "backlog_warn": 10,
        "backlog_major": 50,
        "backlog_critical": 100,
        "enabled": True,
        "remediation": (
            "sudo systemctl status zone-alert-tts\n"
            "sudo journalctl -u zone-alert-tts -n 30"
        ),
    },
]

# ── Default SNMP threshold rules ─────────────────────────────────────────────
DEFAULT_SNMP_RULES = [
    {
        "rule_id": "snmp_ifOperStatus_down",
        "source": "snmp_poller",
        "name": "Interface Down",
        "oid": ".1.3.6.1.2.1.2.2.1.8",
        "comparison": "eq",
        "threshold": 2,       # 1=up, 2=down per IF-MIB
        "severity": "Major",
        "enabled": True,
        "remediation": "Check physical interface and cabling on the device.",
    },
    {
        "rule_id": "snmp_ucd_cpu_load_1min",
        "source": "snmp_poller",
        "name": "High 1-Min CPU Load",
        "oid": ".1.3.6.1.4.1.2021.10.1.3.1",   # UCD-SNMP-MIB laLoad.1
        "comparison": "gt",
        "threshold": 5.0,
        "severity": "Warning",
        "enabled": True,
        "remediation": "Run 'top' on the device to identify CPU-intensive processes.",
    },
    {
        "rule_id": "snmp_ucd_disk_pct",
        "source": "snmp_poller",
        "name": "Disk Usage High",
        "oid": ".1.3.6.1.4.1.2021.9.1.9.1",    # UCD-SNMP-MIB dskPercent.1
        "comparison": "gt",
        "threshold": 90,
        "severity": "Major",
        "enabled": True,
        "remediation": "Run 'df -h' on the device. Clean up logs or expand disk.",
    },
    {
        "rule_id": "snmp_ucd_mem_swap_pct",
        "source": "snmp_poller",
        "name": "Swap Memory High",
        "oid": ".1.3.6.1.4.1.2021.4.4.0",       # UCD-SNMP-MIB memAvailSwap.0
        "comparison": "lt",
        "threshold": 50000,    # kB — alarm when free swap < 50 MB
        "severity": "Warning",
        "enabled": True,
        "remediation": "Check memory pressure: 'free -h'. Consider restarting heavy processes.",
    },
]


class RuleEngine:
    def __init__(self, db):
        self.db = db
        self.col = db["alarm_rules"]
        self._seed_defaults()

    def _seed_defaults(self):
        if self.col.count_documents({}) == 0:
            now = datetime.now(timezone.utc)
            all_rules = DEFAULT_FEED_RULES + DEFAULT_SNMP_RULES
            for r in all_rules:
                r.setdefault("created_at", now)
                r.setdefault("updated_at", now)
            self.col.insert_many(all_rules)
            log.info("Seeded %d default alarm rules.", len(all_rules))

    def get_feed_rules(self) -> list:
        return list(self.col.find({"source": "feed_watchdog", "enabled": True}, {"_id": 0}))

    def get_snmp_rules(self, oid: str = None) -> list:
        query = {"source": "snmp_poller", "enabled": True}
        if oid:
            query["oid"] = oid
        return list(self.col.find(query, {"_id": 0}))

    def upsert_rule(self, rule: dict) -> bool:
        rule_id = rule.get("rule_id")
        if not rule_id:
            return False
        rule["updated_at"] = datetime.now(timezone.utc)
        self.col.update_one({"rule_id": rule_id}, {"$set": rule}, upsert=True)
        return True

    def delete_rule(self, rule_id: str) -> bool:
        return self.col.delete_one({"rule_id": rule_id}).deleted_count > 0

    def list_rules(self, source: str = None) -> list:
        q = {}
        if source:
            q["source"] = source
        return list(self.col.find(q, {"_id": 0}).sort("rule_id", 1))
