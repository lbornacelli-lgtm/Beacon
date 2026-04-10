from .base_agent import BaseAgent

class TrafficAgent(BaseAgent):
    collection_out = "traffic_processed"

    def handle(self, doc):
        raw = (
            f"Location: {doc.get('location', 'Unknown')}\n"
            f"Type: {doc.get('event_type', 'Incident')}\n"
            f"Description: {doc.get('description', '')}\n"
            f"Road: {doc.get('road', '')}\n"
            f"Direction: {doc.get('direction', '')}"
        )
        meta = self.classify(raw)
        severity = meta.get("severity", "low")
        bulletin = self.llm("traffic.md", raw, size="medium", max_tokens=180)
        self.queue_tts(bulletin, voice_category="traffic", priority=6 if severity == "high" else 3)
        self.save({
            "source_id": doc["_id"],
            "bulletin": bulletin,
            "severity": severity,
            "road": doc.get("road", ""),
            "location": doc.get("location", ""),
        })
