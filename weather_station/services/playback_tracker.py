# services/playback_tracker.py
import os
import logging
from datetime import datetime, timezone
from pymongo import MongoClient

logger = logging.getLogger("PlaybackTracker")

ALERT_AUDIO_ROOT = "/home/ufuser/audio_playlist/alerts"


class PlaybackTracker:
    """Records each alert WAV playback to MongoDB for cleanup auditing."""

    def __init__(self):
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        client = MongoClient(mongo_uri)
        self._col = client["weather_rss"]["alert_wav_plays"]

    def record_play(self, wav_path: str):
        """Atomically increment play count and append timestamp for a WAV file."""
        if not wav_path.startswith(ALERT_AUDIO_ROOT):
            return  # Only track alert WAVs
        now = datetime.now(timezone.utc)
        try:
            self._col.update_one(
                {"wav_path": wav_path},
                {
                    "$inc": {"play_count": 1},
                    "$push": {"play_dates": now},
                    "$set": {"last_played": now},
                    "$setOnInsert": {"first_played": now},
                },
                upsert=True,
            )
            logger.debug(f"Recorded play: {os.path.basename(wav_path)}")
        except Exception as e:
            logger.error(f"PlaybackTracker.record_play error: {e}")

    def get_play_info(self, wav_path: str) -> dict:
        """Return play count and dates for a WAV file."""
        try:
            doc = self._col.find_one({"wav_path": wav_path})
            if doc:
                return {
                    "play_count": doc.get("play_count", 0),
                    "play_dates": [
                        d.isoformat() for d in doc.get("play_dates", [])
                    ],
                    "first_played": (
                        doc["first_played"].isoformat()
                        if doc.get("first_played") else None
                    ),
                    "last_played": (
                        doc["last_played"].isoformat()
                        if doc.get("last_played") else None
                    ),
                }
        except Exception as e:
            logger.error(f"PlaybackTracker.get_play_info error: {e}")
        return {"play_count": 0, "play_dates": [], "first_played": None, "last_played": None}
