# ~/weather_station/processing/audio_chain.py

import os
import tempfile
import numpy as np
from pedalboard import Pedalboard, Compressor, Limiter, Gain, HighShelfFilter, LowShelfFilter, Reverb, Delay
import soundfile as sf


def _to_wav(input_file: str) -> tuple:
    """
    Convert any audio file (MP3, WAV) to a numpy array + samplerate.
    Returns (audio_array, samplerate, tmp_file_to_delete)
    """
    ext = os.path.splitext(input_file)[1].lower()
    if ext == ".mp3":
        from pydub import AudioSegment
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        AudioSegment.from_mp3(input_file).export(tmp.name, format="wav")
        audio, samplerate = sf.read(tmp.name)
        return audio, samplerate, tmp.name
    else:
        audio, samplerate = sf.read(input_file)
        return audio, samplerate, None


def apply_audio_chain(input_file: str, output_file: str):
    """
    Apply a professional audio chain to a WAV or MP3 file.
    Output is always WAV. Compatible with Python 3.12 + Pedalboard 0.9.22
    """
    audio, samplerate, tmp = _to_wav(input_file)

    try:
        # Pedalboard chain
        board = Pedalboard([
            Gain(3.0),
            Compressor(threshold_db=-20, ratio=3.0),
            Limiter(threshold_db=-1.0),
            HighShelfFilter(cutoff_frequency_hz=10000, gain_db=4.0),
            LowShelfFilter(cutoff_frequency_hz=120, gain_db=3.0),
            Reverb(room_size=0.3, wet_level=0.2),
            Delay(delay_seconds=0.25, feedback=0.2, mix=0.15)
        ])

        processed_audio = board(audio, samplerate)
        sf.write(output_file, processed_audio, samplerate)
    finally:
        if tmp and os.path.exists(tmp):
            os.remove(tmp)
