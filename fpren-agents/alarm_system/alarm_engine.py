"""
alarm_engine.py — FPREN Central Alarm Processor

Reads from the alarm_events queue collection (written by watchdogs, SNMP
poller, and the Director), applies deduplication and severity logic,
checks maintenance windows, and persists to the alarms collection.

Run as a standalone daemon:
    python3 alarm_engine.py

Alarm lifecycle:
    raised → active → [acknowledged] → cleared → (moved to history after 7 days)

Severity rank (highest first):
    Critical (4) > Major (3) > Minor (2) > Warning (1)
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient, DESCENDING

# Allow relative imports when run as __main__
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from notifier import Notifier
from alarm_rules import RuleEngine

MONGO_URI      = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
POLL_INTERVAL       = 5     # seconds between event queue sweeps
DEDUP_MINUTES       = 10    # suppress duplicate raise within this window
RENOTIFY_CRITICAL   = 30    # re-notify unacked Critical alarms every N minutes
RENOTIFY_MAJOR      = 60    # re-notify unacked Major alarms every N minutes
HISTORY_DAYS        = 7     # move cleared alarms to alarm_history after N days
EVENT_QUEUE_TTL_H   = 24    # delete processed alarm_events older than this many hours

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ALARM-ENGINE] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

SEVERITY_RANK = {"Critical": 4, "Major": 3, "Minor": 2, "Warning": 1}


class AlarmEngine:
    def __init__(self):
        self.client  = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db      = self.client["weather_rss"]
        self.alarms  = self.db["alarms"]
        self.events  = self.db["alarm_events"]
        self.windows = self.db["maintenance_windows"]
        self.history = self.db["alarm_history"]
        self.notifier = Notifier()
        self.rules   = RuleEngine(self.db)
        self._ensure_indexes()
        log.info("AlarmEngine initialized.")

    # ── MongoDB setup ─────────────────────────────────────────────────────────

    def _ensure_indexes(self):
        self.alarms.create_index([("fingerprint", 1), ("status", 1)])
        self.alarms.create_index([("severity", 1), ("raised_at", DESCENDING)])
        self.alarms.create_index([("status", 1), ("raised_at", DESCENDING)])
        self.events.create_index([("processed", 1), ("created_at", 1)])
        self.windows.create_index([("start", 1), ("end", 1)])
        self.history.create_index([("fingerprint", 1), ("cleared_at", DESCENDING)])

    # ── Maintenance windows ───────────────────────────────────────────────────

    def _in_maintenance(self, source: str) -> bool:
        now = datetime.now(timezone.utc)
        return bool(self.windows.find_one({
            "start": {"$lte": now},
            "end":   {"$gte": now},
            "$or":   [{"source": source}, {"source": "*"}],
        }))

    # ── Fingerprint + dedup ───────────────────────────────────────────────────

    @staticmethod
    def _fp(source: str, name: str) -> str:
        return f"{source}::{name}"

    def _find_active(self, fp: str):
        return self.alarms.find_one({
            "fingerprint": fp,
            "status": {"$in": ["active", "acknowledged"]},
        })

    # ── Core raise / clear ────────────────────────────────────────────────────

    def _raise(self, ev: dict):
        source      = ev.get("source", "unknown")
        name        = ev.get("name", "Unnamed Alarm")
        severity    = ev.get("severity", "Warning")
        detail      = ev.get("detail", "")
        remediation = ev.get("remediation", "")
        tags        = ev.get("tags", [])
        now         = datetime.now(timezone.utc)

        if self._in_maintenance(source):
            log.info("Suppressed (maintenance): %s / %s", source, name)
            return

        fp       = self._fp(source, name)
        existing = self._find_active(fp)

        if existing:
            old_rank = SEVERITY_RANK.get(existing["severity"], 0)
            new_rank = SEVERITY_RANK.get(severity, 0)

            if new_rank <= old_rank:
                # Same or lower severity — just bump counter
                self.alarms.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"updated_at": now, "detail": detail},
                     "$inc": {"event_count": 1}},
                )
                return

            # Severity escalation
            self.alarms.update_one(
                {"_id": existing["_id"]},
                {"$set": {
                    "severity": severity,
                    "detail": detail,
                    "updated_at": now,
                    "escalated_at": now,
                }, "$inc": {"event_count": 1}},
            )
            log.warning("ESCALATED [%s→%s] %s / %s", existing["severity"], severity, source, name)
            if new_rank >= SEVERITY_RANK.get("Major", 0):
                self.notifier.send(severity, name, source, detail, remediation,
                                   subject_prefix="[ESCALATED]")
            return

        # Brand-new alarm
        doc = {
            "fingerprint":    fp,
            "source":         source,
            "name":           name,
            "severity":       severity,
            "detail":         detail,
            "remediation":    remediation,
            "tags":           tags,
            "status":         "active",
            "raised_at":      now,
            "updated_at":     now,
            "acknowledged_at": None,
            "acknowledged_by": None,
            "cleared_at":     None,
            "clear_detail":   None,
            "escalated_at":   None,
            "notified_at":    None,
            "event_count":    1,
        }
        result = self.alarms.insert_one(doc)
        log.warning("NEW ALARM [%s] %s / %s", severity, source, name)

        self.notifier.send(severity, name, source, detail, remediation)
        self.alarms.update_one({"_id": result.inserted_id},
                               {"$set": {"notified_at": now}})

    def _clear(self, source: str, name: str, detail: str = ""):
        fp       = self._fp(source, name)
        existing = self._find_active(fp)
        if not existing:
            return
        now = datetime.now(timezone.utc)
        self.alarms.update_one(
            {"_id": existing["_id"]},
            {"$set": {"status": "cleared", "cleared_at": now, "clear_detail": detail}},
        )
        log.info("CLEARED %s / %s", source, name)

        # Send recovery email for Critical/Major alarms
        severity = existing.get("severity", "")
        if severity in {"Critical", "Major"}:
            raised_at = existing.get("raised_at")
            duration_str = ""
            if raised_at:
                delta = now - raised_at
                total_s = int(delta.total_seconds())
                if total_s < 3600:
                    duration_str = f"{total_s // 60}m {total_s % 60}s"
                else:
                    h, rem = divmod(total_s, 3600)
                    duration_str = f"{h}h {rem // 60}m"
            self.notifier.send_clear(severity, existing["name"], source,
                                     detail, duration_str)

    # ── Re-notify unacked Critical / Major ───────────────────────────────────

    def _check_renotify(self):
        now = datetime.now(timezone.utc)
        renotify_map = {
            "Critical": timedelta(minutes=RENOTIFY_CRITICAL),
            "Major":    timedelta(minutes=RENOTIFY_MAJOR),
        }
        for severity, window in renotify_map.items():
            threshold = now - window
            for alarm in self.alarms.find({
                "severity": severity,
                "status":   "active",
                "$or": [{"notified_at": None}, {"notified_at": {"$lt": threshold}}],
            }):
                log.warning("Re-notifying unacked %s: %s", severity, alarm["name"])
                self.notifier.send(
                    alarm["severity"], alarm["name"], alarm["source"],
                    alarm.get("detail", ""), alarm.get("remediation", ""),
                    subject_prefix="[REMINDER]",
                )
                self.alarms.update_one(
                    {"_id": alarm["_id"]},
                    {"$set": {"notified_at": datetime.now(timezone.utc)}},
                )

    # ── Archive old cleared alarms ────────────────────────────────────────────

    def _archive_old(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
        old = list(self.alarms.find({
            "status":     "cleared",
            "cleared_at": {"$lt": cutoff},
        }))
        if old:
            self.history.insert_many(old)
            ids = [d["_id"] for d in old]
            self.alarms.delete_many({"_id": {"$in": ids}})
            log.info("Archived %d cleared alarms to alarm_history.", len(old))
        self._prune_event_queue()

    # ── Prune processed alarm_events queue ───────────────────────────────────

    def _prune_event_queue(self):
        """Delete processed events older than EVENT_QUEUE_TTL_H hours.

        Without pruning the alarm_events collection grows unboundedly — watchdogs
        post one event per 60s per active condition, and processed events are only
        marked with processed=True, never deleted.  This method keeps the queue
        lean so MongoDB scans stay fast.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=EVENT_QUEUE_TTL_H)
        result = self.events.delete_many({
            "processed": True,
            "created_at": {"$lt": cutoff},
        })
        if result.deleted_count:
            log.info("Pruned %d processed events from alarm_events queue.", result.deleted_count)

    # ── Main event processing loop ────────────────────────────────────────────

    def process_events(self):
        pending = list(self.events.find({"processed": False})
                       .sort("created_at", 1).limit(100))
        for ev in pending:
            try:
                action = ev.get("action", "raise")
                if action == "raise":
                    self._raise(ev)
                elif action == "clear":
                    self._clear(ev.get("source", ""), ev.get("name", ""),
                                ev.get("detail", ""))
            except Exception as exc:
                log.error("Event processing error (%s): %s", ev.get("_id"), exc)
            finally:
                self.events.update_one(
                    {"_id": ev["_id"]},
                    {"$set": {"processed": True,
                               "processed_at": datetime.now(timezone.utc)}},
                )

    def run(self):
        log.info("Alarm Engine running — poll every %ds", POLL_INTERVAL)
        tick = 0
        while True:
            try:
                self.process_events()
                tick += 1
                if tick % 12 == 0:          # every ~60s
                    self._check_renotify()
                if tick % 720 == 0:         # every ~60 min
                    self._archive_old()
                    tick = 0
            except Exception as exc:
                log.error("Engine loop error: %s", exc)
            time.sleep(POLL_INTERVAL)


# ── Module-level helper used by Director and other importers ─────────────────

def post_event(db, action: str, source: str, name: str, severity: str = "Warning",
               detail: str = "", remediation: str = "", tags: list = None):
    """
    Post a raise or clear event to the alarm_events queue.
    Called by Director, watchdogs, and SNMP poller.
    """
    db["alarm_events"].insert_one({
        "action":      action,       # "raise" | "clear"
        "source":      source,
        "name":        name,
        "severity":    severity,
        "detail":      detail,
        "remediation": remediation,
        "tags":        tags or [],
        "created_at":  datetime.now(timezone.utc),
        "processed":   False,
    })


if __name__ == "__main__":
    AlarmEngine().run()
