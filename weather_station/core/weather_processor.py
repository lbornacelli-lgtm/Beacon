import logging
from datetime import datetime

class WeatherProcessor:
    def __init__(self, settings, audio_engine):
        self.logger = logging.getLogger("WeatherProcessor")
        self.settings = settings
        self.audio_engine = audio_engine
        self.logger.info("WeatherProcessor initialized")

    def fetch_and_process(self):
        """
        Decide which audio to play:
        - Pending alert WAVs override everything (priority_1 first)
        - Top-of-hour content at :00
        - Educational audio otherwise
        """
        try:
            next_alert = self.audio_engine.file_router.get_next_alert_file()

            if next_alert:
                self.logger.info(f"Alert detected — broadcasting: {next_alert}")
                self.audio_engine.play_alert(next_alert)
            elif datetime.now().minute == 0:
                self.logger.info("Top of hour — playing top_of_hour content")
                self.audio_engine.play_next("top_of_hour")
            else:
                self.logger.info("Playing educational audio")
                self.audio_engine.play_next("educational")

        except Exception as e:
            self.logger.error(f"Error in fetch_and_process: {e}")
