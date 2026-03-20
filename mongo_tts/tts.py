"""
tts.py — Text-to-speech engine for MongoDB TTS Monitor.
Uses ElevenLabs if API key is set, otherwise falls back to gTTS.
"""
import os
import tempfile
from pathlib import Path

from pydub import AudioSegment
from pydub.utils import which
AudioSegment.converter = which("ffmpeg") or "/usr/bin/ffmpeg"
from config import WAV_OUTPUT_DIR

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel (default)

# Per-category voice mapping
CATEGORY_VOICE_MAP = {
    "weather":  "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "traffic":  "ErXwobaYiN019PkySvjV",  # Antoni
    "alerts":   "21m00Tcm4TlvDq8ikWAM",  # Rachel (default)
    "imaging":  "21m00Tcm4TlvDq8ikWAM",  # Rachel (default)
}


def text_to_wav(text: str, filename: str, category: str = "weather") -> Path:
    """Convert text to a .wav file in WAV_OUTPUT_DIR and return its path."""
    if not text or not text.strip():
        raise ValueError("No text provided for TTS conversion.")
    dest = WAV_OUTPUT_DIR / filename
    voice_id = CATEGORY_VOICE_MAP.get(category, ELEVENLABS_VOICE_ID)
    if ELEVENLABS_API_KEY:
        return _elevenlabs_to_wav(text.strip(), dest, voice_id)
    else:
        return _gtts_to_wav(text.strip(), dest)


def _elevenlabs_to_wav(text: str, dest: Path, voice_id: str = ELEVENLABS_VOICE_ID) -> Path:
    """Convert text to WAV using ElevenLabs API."""
    from elevenlabs.client import ElevenLabs
    from elevenlabs import VoiceSettings
    client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    audio = client.text_to_speech.convert(
        voice_id=voice_id,
        text=text,
        model_id="eleven_turbo_v2",
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=0.75,
        ),
    )

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        for chunk in audio:
            tmp.write(chunk)

    try:
        AudioSegment.from_mp3(str(tmp_path)).export(str(dest), format="wav")
    finally:
        tmp_path.unlink(missing_ok=True)

    return dest


def _gtts_to_wav(text: str, dest: Path) -> Path:
    """Convert text to WAV using gTTS (fallback)."""
    from gtts import gTTS

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        gTTS(text=text, lang="en").save(str(tmp_path))
        AudioSegment.from_mp3(str(tmp_path)).export(str(dest), format="wav")
    finally:
        tmp_path.unlink(missing_ok=True)

    return dest

# Map entry types/collections to voice categories
TYPE_TO_CATEGORY = {
    "Construction Zones": "traffic",
    "Incidents":          "traffic",
    "Traffic":            "traffic",
    "weather":            "weather",
    "alert":              "alerts",
}

def convert_entry(entry) -> Path:
    """Convert an entry's description field to a WAV file."""
    description = entry.get("description", "")
    if not description:
        raise ValueError(f"Entry {entry['_id']} has no description field.")
    filename = f"{entry['_id']}.wav"
    # Determine category from type field, falling back to category, then weather
    raw_type = entry.get("type", entry.get("category", "weather"))
    category = TYPE_TO_CATEGORY.get(raw_type, "weather")
    return text_to_wav(description, filename, category)

def tts_engine_name() -> str:
    """Return the name of the active TTS engine."""
    return "ElevenLabs" if ELEVENLABS_API_KEY else "gTTS (fallback)"
