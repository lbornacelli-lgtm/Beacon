import logging
import os
from services.file_router import FileRouter
from processing.audio_chain import apply_audio_chain
from services.tts_engine import TTSEngine
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

    def play_next(self, category="educational"):
        next_file = self.file_router.get_next_file(category)
        if next_file:
            output_file = next_file.replace(".wav", "_processed.wav")
            apply_audio_chain(next_file, output_file)

            # Send processed audio to FM
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Processed and broadcasted: {output_file}")

    def play_alert(self, wav_path: str):
        """Apply audio chain, broadcast the alert, then delete the file so it isn't replayed."""
        output_file = wav_path.replace(".wav", "_processed.wav")
        try:
            apply_audio_chain(wav_path, output_file)
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Alert broadcast: {wav_path}")
        finally:
            for f in (wav_path, output_file):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def play_tts(self, text):
        tts_file = "/tmp/tts_output.wav"
        self.tts_engine.say(text, tts_file)
        self.fm_transmitter.play_wav(tts_file)
