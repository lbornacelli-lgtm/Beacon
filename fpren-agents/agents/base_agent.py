import logging
import os
import sys
from abc import ABC, abstractmethod
from pymongo import MongoClient

# Add project root so weather_station imports resolve
_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from weather_station.services.ai_client import chat, is_configured
from weather_station.config.ai_config import (
    TOKENS_CLASSIFY, MODEL_SMALL, MODEL_MEDIUM, MODEL_LARGE,
)

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

_CLASSIFY_SYSTEM = (
    "You are a classifier for emergency/weather/traffic broadcasts. "
    "Return ONLY a JSON object with keys: "
    "category (weather|traffic|alerts|other), severity (low|medium|high), "
    "tts_priority (true|false). No extra text."
)

_SIZE_MAP = {"small": MODEL_SMALL, "medium": MODEL_MEDIUM, "large": MODEL_LARGE}


class BaseAgent(ABC):
    collection_out = "agent_output"

    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client["weather_rss"]
        self.log = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def handle(self, doc):
        ...

    def llm(self, system_text_or_md: str, user_text: str,
            size: str = "medium", max_tokens: int = 512) -> str:
        """Call the LLM.  system_text_or_md may be a plain string or a .md filename."""
        if system_text_or_md.endswith(".md"):
            from pathlib import Path
            md_path = Path(__file__).parent.parent / "prompts" / system_text_or_md
            system_text_or_md = md_path.read_text(encoding="utf-8")
        return chat(user_text, system=system_text_or_md, size=size, max_tokens=max_tokens)

    def classify(self, text: str) -> dict:
        """Quick JSON classification using the small (fast) model."""
        import json
        if not is_configured():
            return {"category": "other", "severity": "low", "tts_priority": False}
        raw = chat(text, system=_CLASSIFY_SYSTEM, size="small", max_tokens=TOKENS_CLASSIFY * 8)
        try:
            return json.loads(raw)
        except Exception:
            return {"category": "other", "severity": "low", "tts_priority": False}

    def save(self, data: dict):
        self.db[self.collection_out].insert_one(data)
        self.log.info("Saved to %s", self.collection_out)

    def queue_tts(self, text: str, voice_category: str = "weather", priority: int = 5):
        self.db["tts_queue"].insert_one({
            "text": text,
            "voice_category": voice_category,
            "priority": priority,
            "processed": False,
        })
        self.log.info("Queued TTS [%s] priority=%d", voice_category, priority)
