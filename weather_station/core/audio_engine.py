import logging
import os
import tempfile
from services.file_router import FileRouter
from processing.audio_chain import apply_audio_chain
from core.tts_engine import TTSEngine
from services.fm_transmitter import FMTransmitter
from services.icecast_streamer import IcecastStreamer

class AudioEngine:
    def __init__(self, settings):
        self.logger = logging.getLogger("AudioEngine")
        self.settings = settings

        self.file_router = FileRouter(settings)
        self.tts_engine = TTSEngine(settings)

        self._icecast_streamer = IcecastStreamer(settings)
        self._icecast_streamer.start()

        self.fm_transmitter = FMTransmitter(settings, icecast_streamer=self._icecast_streamer)

        self.logger.info("AudioEngine initialized")

    @staticmethod
    def _processed_path(source: str) -> str:
        """Return a /tmp path for the processed WAV output of any source file."""
        stem = os.path.splitext(os.path.basename(source))[0]
        return os.path.join(tempfile.gettempdir(), f"{stem}_processed.wav")

    def play_next(self, category="educational"):
        next_file = self.file_router.get_next_file(category)
        if next_file:
            output_file = self._processed_path(next_file)
            apply_audio_chain(next_file, output_file)
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Broadcasted [{category}]: {next_file}")

    def play_alert(self, wav_path: str):
        """Apply audio chain and broadcast the alert. Source file is NOT deleted
        so zone_alert_tts can keep re-serving it while the alert remains active."""
        output_file = self._processed_path(wav_path)
        try:
            apply_audio_chain(wav_path, output_file)
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Alert broadcast: {wav_path}")
        except Exception as e:
            self.logger.error(f"Alert broadcast failed: {e}")

    def play_tts(self, text):
        tts_file = "/tmp/tts_output.wav"
        self.tts_engine.say(text, tts_file)
        self.fm_transmitter.play_wav(tts_file)
