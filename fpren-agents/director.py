"""
director.py  —  FPREN Traffic Director
Polls MongoDB for new unprocessed documents, classifies them,
and dispatches them to the correct specialized agent.
"""
import time
import logging
from pymongo import MongoClient
from agents.weather_agent import WeatherAgent
from agents.traffic_agent import TrafficAgent
from agents.alerts_agent import AlertsAgent
from agents.tts_agent import TTSAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DIRECTOR] %(message)s")
log = logging.getLogger(__name__)

MONGO_URI = "mongodb://localhost:27017"
POLL_INTERVAL = 5

AGENT_MAP = {
    "nws_alerts": WeatherAgent,
    "airport_metar":   WeatherAgent,
    "fl_traffic":  TrafficAgent,
    "nws_alerts_extended":   AlertsAgent,
    "nws_alerts": AlertsAgent,
    "airport_delays":     AlertsAgent,
    "tts_queue":      TTSAgent,
}

class Director:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client["weather_rss"]
        self._agents = {}

    def _get_agent(self, name):
        if name not in self._agents:
            cls = AGENT_MAP.get(name)
            if not cls:
                return None
            self._agents[name] = cls()
        return self._agents[name]

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
                        log.info("Dispatching %s → %s", doc["_id"], agent.__class__.__name__)
                        agent.handle(doc)
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"processed": True, "processed_by": col_name}}
                        )
                    except Exception as e:
                        log.error("Agent %s failed on %s: %s", col_name, doc["_id"], e)
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"error": str(e), "retry_count": doc.get("retry_count", 0) + 1}}
                        )
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    Director().run()
