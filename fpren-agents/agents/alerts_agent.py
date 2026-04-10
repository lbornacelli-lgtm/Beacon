from .base_agent import BaseAgent

class AlertsAgent(BaseAgent):
    collection_out = "alerts_processed"

    def handle(self, doc):
        source = doc.get("_source_collection", "unknown")
        raw = doc.get("description") or doc.get("message") or str(doc)
        meta = self.classify(raw)
        severity = meta.get("severity", "low")
        size = "large" if severity == "high" else "medium"
        max_tokens = 320 if severity == "high" else 200
        summary = self.llm("alerts.md", f"Source: {source}\n\n{raw}", size=size, max_tokens=max_tokens)
        if severity in ("high", "medium"):
            self.queue_tts(summary, voice_category="alerts", priority=10 if severity == "high" else 5)
        self.save({
            "source_id": doc["_id"],
            "source": source,
            "summary": summary,
            "severity": severity,
            "category": meta.get("category", "other"),
        })
