import os
import requests
from pathlib import Path
from pydub import AudioSegment
from .base_agent import BaseAgent

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_BASE    = "https://api.elevenlabs.io/v1"

VOICE_IDS = {
    "weather": os.getenv("EL_VOICE_WEATHER", "21m00Tcm4TlvDq8ikWAM"),
    "traffic": os.getenv("EL_VOICE_TRAFFIC", "AZnzlk1XvdvUeBnXmlld"),
    "alerts":  os.getenv("EL_VOICE_ALERTS",  "EXAVITQu4vr4xnSDxMaL"),
    "default": os.getenv("EL_VOICE_DEFAULT", "21m00Tcm4TlvDq8ikWAM"),
}

AUDIO_BASE = Path(os.getenv("FPREN_AUDIO_DIR", "/home/ufuser/Fpren-main/weather_station/audio/alerts"))
AUDIO_DIRS = {
    "weather": AUDIO_BASE / "weather",
    "traffic": AUDIO_BASE / "traffic",
    "alerts":  AUDIO_BASE / "other_alerts",
    "default": AUDIO_BASE / "other_alerts",
}

class TTSAgent(BaseAgent):
    collection_out = "tts_completed"

    def handle(self, doc):
        text     = doc.get("text", "").strip()
        category = doc.get("voice_category", "default")
        priority = doc.get("priority", 3)
        if not text:
            self.log.warning("Empty TTS text in doc %s — skipping", doc["_id"])
            return
        voice_id = VOICE_IDS.get(category, VOICE_IDS["default"])
        out_dir  = AUDIO_DIRS.get(category, AUDIO_DIRS["default"])
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"tts_{doc['_id']}"
        mp3_path = out_dir / f"{filename}.mp3"
        wav_path = out_dir / f"{filename}.wav"
        self._elevenlabs_to_mp3(text, voice_id, mp3_path)
        audio = AudioSegment.from_mp3(str(mp3_path))
        audio.export(str(wav_path), format="wav")
        mp3_path.unlink(missing_ok=True)
        self.log.info("WAV written: %s (priority=%d)", wav_path.name, priority)
        self.save({"source_id": doc["_id"], "wav_path": str(wav_path),
                   "category": category, "priority": priority, "char_count": len(text)})

    def _elevenlabs_to_mp3(self, text, voice_id, dest):
        resp = requests.post(
            f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_monolingual_v1",
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}},
            timeout=30,
        )
        resp.raise_for_status()
        dest.write_bytes(resp.content)
