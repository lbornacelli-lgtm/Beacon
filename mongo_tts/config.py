from pathlib import Path

MONGO_URI       = "mongodb://localhost:27017"
DB_NAME         = "tts_db"
COLLECTION      = "entries"
WAV_OUTPUT_DIR  = Path.home() / "wav_output"
FLASK_PORT      = 5001
FLASK_DEBUG     = True

# Ensure output dir exists
WAV_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
