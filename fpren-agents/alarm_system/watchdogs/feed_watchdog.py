"""
feed_watchdog.py — FPREN Feed & Agent Staleness Watchdog

Monitors MongoDB beacon collections for stale updates and raises alarms
via the alarm_events queue when feeds fall behind their expected schedules.

Checks:
  • Collection field staleness vs. alarm_rules thresholds
  • TTS queue backlog depth
  • NWS fetcher, Waze fetcher, river fetcher last-run timestamps
  • agent execution_log recency (Director agent health)

Run interval: 60 seconds (configured via FEED_WD_INTERVAL env var).
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta

from pymongo import MongoClient

_HERE = os.path.dirname(os.path.abspath(__file__))
_ALARM_SYS = os.path.dirname(_HERE)
if _ALARM_SYS not in sys.path:
    sys.path.insert(0, _ALARM_SYS)

from alarm_engine import post_event
from alarm_rules import RuleEngine

MONGO_URI    = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
INTERVAL     = int(os.environ.get("FEED_WD_INTERVAL", "60"))
SOURCE       = "feed_watchdog"

# Director agent health — alarm if no successful dispatch in this many minutes
DIRECTOR_STALE_WARN_MIN     = 10
DIRECTOR_STALE_MAJOR_MIN    = 30
DIRECTOR_STALE_CRITICAL_MIN = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [FEED-WD] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def _age_minutes(dt) -> float | None:
    """Return age in minutes of a datetime value. Handles naive + aware datetimes."""
    if dt is None:
        return None
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 60.0


def _severity_from_age(age_min: float, rule: dict) -> str | None:
    """Map staleness age to severity using rule thresholds. Returns None if below warn."""
    if age_min >= rule.get("critical_minutes", 9999):
        return "Critical"
    if age_min >= rule.get("major_minutes", 9999):
        return "Major"
    if age_min >= rule.get("minor_minutes", 9999):
        return "Minor"
    if age_min >= rule.get("warn_minutes", 9999):
        return "Warning"
    return None


class FeedWatchdog:
    def __init__(self):
        self.client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        self.db     = self.client["weather_rss"]
        self.rules  = RuleEngine(self.db)
        log.info("FeedWatchdog initialized — checking every %ds", INTERVAL)

    # ── Staleness check ───────────────────────────────────────────────────────

    def _check_staleness(self, rule: dict):
        col_name = rule["collection"]
        field    = rule["field"]
        name     = rule["name"]

        # Get the most recent document by the timestamp field
        try:
            doc = self.db[col_name].find_one(
                {field: {"$exists": True, "$ne": None}},
                sort=[(field, -1)],
            )
        except Exception as exc:
            post_event(self.db, "raise", SOURCE, f"Collection Query Error: {col_name}",
                       severity="Major",
                       detail=f"Failed to query {col_name}.{field}: {exc}",
                       remediation="Check MongoDB connectivity and collection health.")
            return

        if doc is None:
            post_event(self.db, "raise", SOURCE, name,
                       severity="Major",
                       detail=f"Collection '{col_name}' is empty — no documents found.",
                       remediation=rule.get("remediation", ""))
            return

        ts = doc.get(field)
        if ts is None:
            return

        # Handle both datetime objects and ISO strings
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                return

        age = _age_minutes(ts)
        if age is None:
            return

        sev = _severity_from_age(age, rule)
        if sev:
            post_event(
                self.db, "raise", SOURCE, name,
                severity=sev,
                detail=(f"Collection '{col_name}' last update was {age:.1f} minutes ago "
                        f"(field: {field}, value: {ts.isoformat()})."),
                remediation=rule.get("remediation", ""),
                tags=["feed", col_name],
            )
        else:
            post_event(self.db, "clear", SOURCE, name)

    # ── TTS backlog check ─────────────────────────────────────────────────────

    def _check_backlog(self, rule: dict):
        name     = rule["name"]
        col_name = rule["collection"]
        try:
            count = self.db[col_name].count_documents({"processed": {"$ne": True}})
        except Exception as exc:
            log.error("Backlog count error for %s: %s", col_name, exc)
            return

        crit  = rule.get("backlog_critical", 100)
        major = rule.get("backlog_major", 50)
        warn  = rule.get("backlog_warn", 10)

        if count >= crit:
            sev = "Critical"
        elif count >= major:
            sev = "Major"
        elif count >= warn:
            sev = "Warning"
        else:
            post_event(self.db, "clear", SOURCE, name)
            return

        post_event(
            self.db, "raise", SOURCE, name,
            severity=sev,
            detail=f"Collection '{col_name}' has {count} unprocessed documents (backlog).",
            remediation=rule.get("remediation", ""),
            tags=["backlog", col_name],
        )

    # ── Director agent health ─────────────────────────────────────────────────

    def _check_director(self):
        alarm_name = "Director Agent Stale"
        try:
            # execution_logger records status as "ok" (not "success")
            doc = self.db["execution_log"].find_one(
                {"status": "ok"},
                sort=[("timestamp", -1)],
            )
        except Exception as exc:
            log.error("execution_log query error: %s", exc)
            return

        if doc is None:
            # Director has no systemd service — it is run manually.
            # Only raise if there is at least one failed entry (indicating it ran but errored),
            # not if the log is simply empty (meaning it was never started).
            has_any = self.db["execution_log"].count_documents({}) > 0
            if not has_any:
                return   # Director never ran — not an alarm condition
            post_event(
                self.db, "raise", SOURCE, alarm_name,
                severity="Major",
                detail="Director has entries in execution_log but no recent successful dispatch.",
                remediation=(
                    "cd ~/Fpren-main && source venv/bin/activate\n"
                    "python3 fpren-agents/director.py\n"
                    "Check LiteLLM connectivity and agent logs."
                ),
                tags=["director", "agent"],
            )
            return

        ts  = doc.get("timestamp")
        if ts is None:
            return
        age = _age_minutes(ts)

        if age >= DIRECTOR_STALE_CRITICAL_MIN:
            sev = "Critical"
        elif age >= DIRECTOR_STALE_MAJOR_MIN:
            sev = "Major"
        elif age >= DIRECTOR_STALE_WARN_MIN:
            sev = "Warning"
        else:
            post_event(self.db, "clear", SOURCE, alarm_name)
            return

        post_event(
            self.db, "raise", SOURCE, alarm_name,
            severity=sev,
            detail=f"Director last successful dispatch was {age:.1f} minutes ago.",
            remediation=(
                "sudo systemctl status fpren-director\n"
                "sudo journalctl -u fpren-director -n 30\n"
                "sudo systemctl restart fpren-director"
            ),
            tags=["director", "agent"],
        )

    # ── MongoDB connectivity check ────────────────────────────────────────────

    def _check_mongodb(self):
        alarm_name = "MongoDB Connectivity"
        try:
            self.client.admin.command("ping")
            post_event(self.db, "clear", SOURCE, alarm_name)
        except Exception as exc:
            post_event(
                self.db, "raise", SOURCE, alarm_name,
                severity="Critical",
                detail=f"MongoDB ping failed: {exc}",
                remediation=(
                    "sudo systemctl status mongod\n"
                    "sudo systemctl restart mongod\n"
                    "sudo journalctl -u mongod -n 30"
                ),
                tags=["mongodb", "database"],
            )

    # ── Main loop ─────────────────────────────────────────────────────────────

    def check_all(self):
        self._check_mongodb()
        for rule in self.rules.get_feed_rules():
            check_type = rule.get("check_type", "staleness")
            try:
                if check_type == "staleness":
                    self._check_staleness(rule)
                elif check_type == "backlog":
                    self._check_backlog(rule)
            except Exception as exc:
                log.error("Rule check error [%s]: %s", rule.get("rule_id"), exc)
        self._check_director()

    def run(self):
        log.info("FeedWatchdog running.")
        while True:
            try:
                self.check_all()
            except Exception as exc:
                log.error("Watch loop error: %s", exc)
            time.sleep(INTERVAL)


if __name__ == "__main__":
    FeedWatchdog().run()
