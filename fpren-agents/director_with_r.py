import time
import logging
from datetime import datetime
from pymongo import MongoClient
from agents.weather_agent import WeatherAgent
from agents.traffic_agent import TrafficAgent
from agents.alerts_agent import AlertsAgent
from agents.tts_agent import TTSAgent
from r_bridge import RBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s [DIRECTOR] %(message)s")
log = logging.getLogger(__name__)

MONGO_URI     = "mongodb://localhost:27017"
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
        self.client       = MongoClient(MONGO_URI)
        self.db           = self.client["weather_rss"]
        self._agents      = {}
        self.r            = RBridge()
        self._last_daily  = None
        self._last_weekly = None
        self._last_stats  = None

    def _get_agent(self, name):
        if name not in self._agents:
            cls = AGENT_MAP.get(name)
            if not cls:
                return None
            self._agents[name] = cls()
        return self._agents[name]

    def _check_scheduled_r(self):
        now = datetime.now()
        if now.hour == 6 and now.minute < 1:
            today = now.date()
            if self._last_daily != today:
                log.info("Triggering daily R report render")
                self.r.render_daily()
                self._last_daily = today
        if now.weekday() == 0 and now.hour == 6 and 30 <= now.minute < 31:
            today = now.date()
            if self._last_weekly != today:
                log.info("Triggering weekly R report render")
                self.r.render_weekly()
                self._last_weekly = today
        if now.minute < 1:
            hour_key = (now.date(), now.hour)
            if self._last_stats != hour_key:
                log.info("Running hourly R stats modules")
                self.r.run_module("alert_stats")
                self.r.run_module("traffic_stats")
                self._last_stats = hour_key

    def run(self):
        log.info("Director (R-integrated) started — watching: %s", list(AGENT_MAP))
        while True:
            self._check_scheduled_r()
            for col_name in AGENT_MAP:
                collection = self.db[col_name]
                pending = collection.find({"processed": {"$ne": True}}).limit(20)
                for doc in pending:
                    agent = self._get_agent(col_name)
                    if not agent:
                        continue
                    try:
                        log.info("Dispatching %s -> %s", doc["_id"], agent.__class__.__name__)
                        agent.handle(doc)
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"processed": True}}
                        )
                    except Exception as e:
                        log.error("Agent failed on %s: %s", doc["_id"], e)
                        collection.update_one(
                            {"_id": doc["_id"]},
                            {"$set": {"error": str(e),
                                      "retry_count": doc.get("retry_count", 0) + 1}}
                        )
            time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    Director().run()
