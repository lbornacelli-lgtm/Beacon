import logging
import subprocess
import os

class FMTransmitter:
    def __init__(self, settings, icecast_streamer=None):
        self.logger = logging.getLogger("FMTransmitter")
        self.settings = settings
        self._icecast_streamer = icecast_streamer
        self.logger.info("FMTransmitter initialized")

    def play_wav(self, wav_file):
        """Enqueue wav_file to Icecast. Also attempts FM hardware playback if available."""
        if not os.path.isfile(wav_file):
            self.logger.warning(f"File does not exist: {wav_file}")
            return

        # Icecast is the primary output — enqueue first so it is never skipped
        if self._icecast_streamer:
            self._icecast_streamer.enqueue(wav_file)
            self.logger.info(f"Queued for Icecast: {wav_file}")

        # FM hardware is optional; failures are logged but never raise
        try:
            cmd = ["aplay", "-D", "plughw:0,3", wav_file]
            subprocess.run(cmd, check=True, timeout=120)
            self.logger.info(f"FM broadcast: {wav_file}")
        except FileNotFoundError:
            pass  # aplay not installed — expected in VM-only deployment
        except Exception as e:
            self.logger.debug(f"FM hardware unavailable: {e}")
