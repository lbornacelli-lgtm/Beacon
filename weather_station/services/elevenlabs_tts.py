#!/usr/bin/env python3
# weather_station/services/elevenlabs_tts.py
"""
elevenlabs_tts.py
-----------------
ElevenLabs TTS wrapper for high-severity alerts.
Used by zone_alert_tts.py when ai_classifier routes to ElevenLabs.

Environment variables:
    ELEVENLABS_API_KEY   — ElevenLabs API key (already in .bashrc)
    ELEVENLABS_VOICE_ID  — Voice ID to use (default: Rachel)
    ELEVENLABS_MODEL_ID  — Model ID (default: eleven_multilingual_v2)
"""

import logging
import os
import requests

logger = logging.getLogger("elevenlabs_tts")

API_KEY  = os.getenv("ELEVENLABS_API_KEY", "")
VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Rachel
MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

TTS_URL  = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"


def is_configured() -> bool:
    return bool(API_KEY)


def synthesise(text: str, output_path: str) -> bool:
    """
    Generate speech from text using ElevenLabs and save to output_path.
    Returns True on success, False on failure.
    """
    if not is_configured():
        logger.warning("ElevenLabs API key not set — cannot synthesise")
        return False

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    headers = {
        "xi-api-key":   API_KEY,
        "Content-Type": "application/json",
        "Accept":       "audio/mpeg",
    }
    payload = {
        "text":     text,
        "model_id": MODEL_ID,
        "voice_settings": {
            "stability":        0.5,
            "similarity_boost": 0.75,
            "style":            0.0,
            "use_speaker_boost": True,
        },
    }

    try:
        resp = requests.post(TTS_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        tmp = output_path + ".tmp.mp3"
        with open(tmp, "wb") as f:
            f.write(resp.content)
        os.replace(tmp, output_path)
        logger.info("ElevenLabs TTS saved: %s (%d bytes)", output_path, len(resp.content))
        return True
    except requests.RequestException as e:
        logger.error("ElevenLabs TTS failed: %s", e)
        if os.path.exists(output_path + ".tmp.mp3"):
            os.unlink(output_path + ".tmp.mp3")
        return False


def say(text: str, output_file: str) -> str | None:
    """
    Drop-in replacement for TTSService.say() for ElevenLabs.
    Returns output_file path on success, None on failure.
    """
    if synthesise(text, output_file):
        return output_file
    return None
