"""
director.py  —  FPREN Traffic Director
Polls MongoDB for new unprocessed documents, classifies them,
and dispatches them to the correct specialized agent.
"""
import time
import logging
import sys
import os
from pymongo import MongoClient
from agents.weather_agent import WeatherAgent
from agents.traffic_agent import TrafficAgent
from agents.alerts_agent import AlertsAgent
from agents.tts_agent import TTSAgent
from execution_logger import log_agent_call, patch_litellm_router
import routing_adapter

# Alarm engine integration — post agent failures to alarm_events queue
_ALARM_SYS = os.path.join(os.path.dirname(__file__), "alarm_system")
if _ALARM_SYS not in sys.path:
    sys.path.insert(0, _ALARM_SYS)
try:
    from alarm_engine import post_event as _alarm_post
    _ALARM_ENABLED = True
except ImportError:
    _ALARM_ENABLED = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DIRECTOR] %(message)s")
log = logging.getLogger(__name__)

MONGO_URI = "mongodb://localhost:27017"
POLL_INTERVAL = 5

AGENT_MAP = {
    "nws_alerts":          AlertsAgent,
    "nws_alerts_extended": AlertsAgent,
    "airport_delays":      AlertsAgent,
    "airport_metar":       WeatherAgent,
    "fl_traffic":          TrafficAgent,
    "tts_queue":           TTSAgent,
}

class Director:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client["weather_rss"]
        self._agents = {}
        patch_litellm_router()
        routing_adapter.run()

    def _get_agent(self, name):
        if name not in self._agents:
            cls = AGENT_MAP.get(name)
            if not cls:
                return None
            self._agents[name] = cls()
        return self._agents[name]

    @log_agent_call
    def _dispatch(self, agent, doc):
        """Single agent call — wrapped by execution_logger for timing and logging."""
        agent.handle(doc)

    def run(self):
        log.info("Director started — watching: %s", list(AGENT_MAP))
        while True:
            for col_name in AGENT_MAP:
                collection = self.db[col_name]
                pending = collection.find({"processed": {"$ne": True}}).limit(20)
                for doc in pending:
                    agent = self._get_agent(col_name)
                    if not agent:
                        continue
                    try:
                        doc["_source_collection"] = col_name
                        log.info("Dispatching %s → %s", doc["_id"], agent.__class__.__name__)
                        self._dispatch(agent, doc)
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"processed": True, "processed_by": col_name}}
                        )
                    except Exception as e:
                        log.error("Agent %s failed on %s: %s", col_name, doc["_id"], e)
                        retry = doc.get("retry_count", 0) + 1
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"error": str(e), "retry_count": retry}}
                        )
                        if _ALARM_ENABLED and retry >= 3:
                            _alarm_post(
                                self.db, "raise",
                                source="director",
                                name=f"Agent Failure: {col_name}",
                                severity="Major" if retry >= 5 else "Minor",
                                detail=(f"Agent {agent.__class__.__name__} failed "
                                        f"{retry} times on collection '{col_name}'.\n"
                                        f"Last error: {e}"),
                                remediation=(
                                    "sudo journalctl -u fpren-director -n 30\n"
                                    "Check LiteLLM connectivity and MongoDB collection health."
                                ),
                                tags=["director", col_name, "agent-failure"],
                            )
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    Director().run()
