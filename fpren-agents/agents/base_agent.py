import logging
from abc import ABC, abstractmethod
from pymongo import MongoClient
from litellm_router import complete, classify

MONGO_URI = "mongodb://localhost:27017"

class BaseAgent(ABC):
    collection_out = "agent_output"

    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client["weather_rss"]
        self.log = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def handle(self, doc):
        ...

    def llm(self, system_md, user_text, size="medium", max_tokens=512):
        return complete(system_md, user_text, size=size, max_tokens=max_tokens)

    def classify(self, text):
        return classify(text)

    def save(self, data):
        self.db[self.collection_out].insert_one(data)
        self.log.info("Saved to %s", self.collection_out)

    def queue_tts(self, text, voice_category="weather", priority=5):
        self.db["tts_queue"].insert_one({
            "text": text,
            "voice_category": voice_category,
            "priority": priority,
            "processed": False,
        })
        self.log.info("Queued TTS [%s] priority=%d", voice_category, priority)
