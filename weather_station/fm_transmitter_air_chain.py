"""
fm_transmitter_air_chain.py

Orchestrates the full FM broadcast pipeline:
    XML → Mongo → xml_parser → tts_engine → processing/audio_chain
        → playlist_engine → interrupt_engine → fm_engine → FM Transmitter

Usage:
    from weather_station.fm_transmitter_air_chain import FMAirChain
    chain = FMAirChain(settings)
    chain.broadcast("Severe thunderstorm warning in effect until 9 PM.")
"""

import logging
import os
import tempfile

from weather_station.core.tts_engine import TTSEngine
from weather_station.processing.audio_chain import apply_audio_chain
from weather_station.core.fm_engine import send_to_transmitter

logger = logging.getLogger(__name__)


class FMAirChain:
    """End-to-end pipeline from text to FM transmission.

    Steps:
        1. TTS  — converts text to a raw WAV file
        2. Audio chain — applies compression, limiting, EQ, reverb
        3. FM transmitter — sends processed audio to the FM device
    """

    def __init__(self, settings):
        self.settings = settings
        self.tts = TTSEngine(settings)
        logger.info("FMAirChain ready.")

    def broadcast(self, text: str) -> bool:
        """Run the full air chain for a given text string.

        Args:
            text: The text to synthesize and broadcast.

        Returns:
            True if the full pipeline succeeded, False otherwise.
        """
        if not text or not text.strip():
            logger.warning("broadcast() called with empty text — skipping.")
            return False

        raw_wav = None
        processed_wav = None

        try:
            # Step 1: TTS → raw WAV
            with tempfile.NamedTemporaryFile(suffix="_raw.wav", delete=False) as f:
                raw_wav = f.name
            result = self.tts.say(text, output_file=raw_wav)
            if not result or not os.path.exists(raw_wav):
                logger.error("TTS failed — aborting air chain.")
                return False
            logger.info("Step 1 complete: TTS → %s", raw_wav)

            # Step 2: Audio chain → processed WAV
            with tempfile.NamedTemporaryFile(suffix="_processed.wav", delete=False) as f:
                processed_wav = f.name
            apply_audio_chain(raw_wav, processed_wav)
            if not os.path.exists(processed_wav):
                logger.error("Audio chain failed — aborting.")
                return False
            logger.info("Step 2 complete: audio chain → %s", processed_wav)

            # Step 3: Send to FM transmitter
            send_to_transmitter(processed_wav)
            logger.info("Step 3 complete: transmitted to FM.")
            return True

        except Exception as e:
            logger.exception("FMAirChain pipeline error: %s", e)
            return False

        finally:
            # Always clean up temp files
            for path in (raw_wav, processed_wav):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError as e:
                        logger.warning("Could not delete temp file %s: %s", path, e)
