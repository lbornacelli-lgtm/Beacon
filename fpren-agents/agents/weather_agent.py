from .base_agent import BaseAgent

class WeatherAgent(BaseAgent):
    collection_out = "weather_processed"

    def handle(self, doc):
        raw = doc.get("description") or doc.get("headline") or str(doc)
        meta = self.classify(raw)
        severity = meta.get("severity", "low")
        summary = self.llm("weather.md", raw, size="medium", max_tokens=256)
        if severity in ("high", "medium"):
            self.queue_tts(summary, voice_category="weather", priority=9 if severity == "high" else 5)
        self.save({
            "source_id": doc["_id"],
            "summary": summary,
            "severity": severity,
            "raw_headline": doc.get("headline", ""),
            "zone": doc.get("zone", ""),
        })
