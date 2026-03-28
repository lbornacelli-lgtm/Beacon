import shutil
import tempfile
from pathlib import Path
from gtts import gTTS
from pydub import AudioSegment
from config import WAV_OUTPUT_DIR


def text_to_wav(text: str, filename: str) -> Path:
    """Convert text to a .wav file in WAV_OUTPUT_DIR and return its path."""
    if not text or not text.strip():
        raise ValueError("No text provided for TTS conversion.")

    dest = WAV_OUTPUT_DIR / filename

    # gTTS generates MP3; convert to WAV via pydub
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        gTTS(text=text.strip(), lang="en").save(str(tmp_path))
        AudioSegment.from_mp3(str(tmp_path)).export(str(dest), format="wav")
    finally:
        tmp_path.unlink(missing_ok=True)

    return dest


def convert_entry(entry) -> Path:
    """Convert an entry's description field to a WAV file."""
    description = entry.get("description", "")
    if not description:
        raise ValueError(f"Entry {entry['_id']} has no description field.")

    filename = f"{entry['_id']}.wav"
    return text_to_wav(description, filename)
