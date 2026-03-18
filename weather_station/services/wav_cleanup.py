# services/wav_cleanup.py
import os
import json
import logging
from datetime import datetime, timezone, timedelta
from services.playback_tracker import PlaybackTracker

logger = logging.getLogger("WavCleanup")

ALERT_AUDIO_ROOT = "/home/ufuser/Downloads/Beacon-main/weather_station/audio/alerts"
DELETION_LOG     = "/home/ufuser/Downloads/Beacon-main/weather_station/logs/wav_deletions.jsonl"
MAX_AGE_DAYS     = 3


def run_cleanup():
    """
    Walk audio_playlist/alerts/, delete WAV files older than MAX_AGE_DAYS,
    and append one JSON record per deleted file to wav_deletions.jsonl.

    Each log entry contains:
        wav_path, filename, event_type (subfolder), created_at, deleted_at,
        play_count, play_dates, first_played, last_played
    """
    tracker = PlaybackTracker()
    cutoff  = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    os.makedirs(os.path.dirname(DELETION_LOG), exist_ok=True)

    deleted = 0

    for root, _, files in os.walk(ALERT_AUDIO_ROOT):
        for fname in sorted(files):
            if not fname.lower().endswith(".wav"):
                continue

            fpath = os.path.join(root, fname)
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath), tz=timezone.utc)
            except OSError:
                continue

            if mtime >= cutoff:
                continue  # Not old enough

            play_info  = tracker.get_play_info(fpath)
            event_type = os.path.basename(root)  # subfolder = event type

            log_entry = {
                "wav_path":    fpath,
                "filename":    fname,
                "event_type":  event_type,
                "created_at":  mtime.isoformat(),
                "deleted_at":  datetime.now(timezone.utc).isoformat(),
                "play_count":  play_info["play_count"],
                "play_dates":  play_info["play_dates"],
                "first_played": play_info["first_played"],
                "last_played":  play_info["last_played"],
            }

            try:
                os.remove(fpath)
                with open(DELETION_LOG, "a") as f:
                    f.write(json.dumps(log_entry) + "\n")
                age_days = (datetime.now(timezone.utc) - mtime).days
                logger.info(
                    f"Deleted {fname} "
                    f"(age={age_days}d, plays={play_info['play_count']}, type={event_type})"
                )
                deleted += 1
            except OSError as e:
                logger.error(f"Failed to delete {fpath}: {e}")

    logger.info(f"Cleanup complete: {deleted} file(s) deleted.") if deleted else \
        logger.debug("Cleanup: no files eligible for deletion.")
