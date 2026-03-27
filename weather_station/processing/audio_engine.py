import os
import logging
from processing.audio_chain import apply_audio_chain


class AudioEngine:
    def __init__(self, settings):
        self.settings = settings
        self.fm_transmitter = None
        self.tts_engine = None
        self.logger = logging.getLogger("AudioEngine")

    def _processed_path(self, input_file: str) -> str:
        """Return output path for processed audio — always WAV."""
        base = os.path.splitext(input_file)[0]
        return base + "_processed.wav"

    def play_next(self, category="educational"):
        next_file = self._get_next_file(category)
        if not next_file:
            return
        output_file = self._processed_path(next_file)
        apply_audio_chain(next_file, output_file)
        self.fm_transmitter.play_wav(output_file)

    def play_alert(self, audio_path: str):
        """
        Apply audio chain and broadcast alert.
        Accepts both .wav and .mp3 files.
        Deletes source and processed file after broadcast.
        """
        output_file = self._processed_path(audio_path)
        try:
            apply_audio_chain(audio_path, output_file)
            self.fm_transmitter.play_wav(output_file)
            self.logger.info(f"Alert broadcast: {audio_path}")
        except Exception as e:
            self.logger.error(f"Alert playback failed: {e}")
        finally:
            for f in (audio_path, output_file):
                if f and os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError:
                        pass

    def play_tts(self, text):
        """Generate TTS and broadcast — uses ElevenLabs (MP3) or Piper (WAV)."""
        tts_file = self._generate_tts(text)
        if tts_file and os.path.exists(tts_file):
            self.play_alert(tts_file)

    def _generate_tts(self, text: str) -> str:
        """Generate TTS audio using ElevenLabs (MP3) or Piper (WAV)."""
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

        if api_key:
            return self._tts_elevenlabs(text, api_key, voice_id)
        else:
            return self._tts_piper(text)

    def _tts_elevenlabs(self, text: str, api_key: str, voice_id: str) -> str:
        """Generate MP3 from ElevenLabs."""
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs import VoiceSettings

            client = ElevenLabs(api_key=api_key)
            audio  = client.text_to_speech.convert(
                voice_id=voice_id,
                text=text,
                model_id="eleven_turbo_v2",
                voice_settings=VoiceSettings(stability=0.5, similarity_boost=0.75),
            )
            out = "/tmp/tts_elevenlabs.mp3"
            with open(out, "wb") as f:
                for chunk in audio:
                    f.write(chunk)
            self.logger.info("ElevenLabs TTS generated: %s", out)
            return out
        except Exception as e:
            self.logger.error("ElevenLabs TTS failed: %s", e)
            return self._tts_piper(text)

    def _tts_piper(self, text: str) -> str:
        """Generate WAV from Piper (fallback)."""
        try:
            import wave
            from piper import PiperVoice
            voice_model = os.getenv(
                "PIPER_VOICE_MODEL",
                "/home/ufuser/Fpren-main/weather_station/voices/en_US-amy-medium.onnx"
            )
            voice = PiperVoice.load(voice_model)
            out = "/tmp/tts_piper.wav"
            with wave.open(out, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(voice.config.sample_rate)
                for chunk in voice.synthesize(text):
                    wf.writeframes(chunk.audio_int16_bytes)
            return out
        except Exception as e:
            self.logger.error("Piper TTS failed: %s", e)
            return None

    def _get_next_file(self, category: str) -> str:
        """Get next audio file from category folder (WAV or MP3)."""
        audio_path = os.path.join(self.settings.AUDIO_PATH, category)
        if not os.path.isdir(audio_path):
            return None
        files = [
            f for f in os.listdir(audio_path)
            if f.lower().endswith((".wav", ".mp3"))
        ]
        if not files:
            return None
        return os.path.join(audio_path, files[0])
