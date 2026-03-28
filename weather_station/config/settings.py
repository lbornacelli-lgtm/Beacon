import os
from dotenv import load_dotenv

load_dotenv()  # load .env variables

class Settings:
    WATCHDOG_PATH = os.getenv("WATCHDOG_PATH", "/tmp/weather_station.watchdog")
    FETCH_INTERVAL_SECONDS = int(os.getenv("FETCH_INTERVAL_SECONDS", 60))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    AUDIO_PATH = os.getenv("AUDIO_PATH", "/home/lh_admin/weather_station/audio")

    # Icecast streaming
    ICECAST_HOST            = os.getenv("ICECAST_HOST", "localhost")
    ICECAST_PORT            = int(os.getenv("ICECAST_PORT", "8000"))
    ICECAST_MOUNT           = os.getenv("ICECAST_MOUNT", "/beacon")
    ICECAST_SOURCE_PASSWORD = os.getenv("ICECAST_SOURCE_PASSWORD", "")
