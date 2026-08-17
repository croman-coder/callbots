"""Configuración del voice-agent. Todo por variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass

# --- Constantes del protocolo AudioSocket de Asterisk ---
# Asterisk entrega y espera slin: PCM 16-bit signed, mono, little-endian, 8 kHz.
SAMPLE_RATE = 8000
SAMPLE_WIDTH = 2
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000      # 160
BYTES_PER_FRAME = SAMPLES_PER_FRAME * SAMPLE_WIDTH      # 320

# Whisper trabaja a 16 kHz
WHISPER_SAMPLE_RATE = 16000


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "y")


@dataclass(frozen=True)
class Config:
    api_base_url: str = os.getenv("API_BASE_URL", "http://api:8000")
    internal_token: str = os.getenv("INTERNAL_TOKEN", "dev-internal-token")

    listen_host: str = os.getenv("AUDIOSOCKET_HOST", "0.0.0.0")
    listen_port: int = int(os.getenv("AUDIOSOCKET_PORT", "8090"))

    # --- STT ---
    whisper_model: str = os.getenv("WHISPER_MODEL", "medium")
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cpu")
    whisper_compute_type: str = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
    whisper_language: str = os.getenv("WHISPER_LANGUAGE", "es")

    # --- TTS ---
    # Voicebox (voz clonada). Si VOICEBOX_URL y VOICEBOX_PROFILE_ID están
    # seteados es el motor principal; Piper queda de respaldo.
    voicebox_url: str = os.getenv("VOICEBOX_URL", "")
    voicebox_profile_id: str = os.getenv("VOICEBOX_PROFILE_ID", "")
    voicebox_engine: str = os.getenv("VOICEBOX_ENGINE", "qwen")
    voicebox_language: str = os.getenv("VOICEBOX_LANGUAGE", "es")
    # Precalentado: nadie espera, puede tardar lo que haga falta.
    voicebox_warmup_timeout: int = int(os.getenv("VOICEBOX_WARMUP_TIMEOUT", "300"))
    # En llamada: hay un cliente escuchando. Si voicebox no contesta en este
    # tiempo, habla Piper. Una frase robótica es mejor que medio minuto de silencio.
    voicebox_call_timeout: int = int(os.getenv("VOICEBOX_CALL_TIMEOUT", "8"))

    # Piper (respaldo, y motor principal si no hay voicebox)
    piper_voice: str = os.getenv("PIPER_VOICE", "es_AR-daniela-high")
    piper_dir: str = os.getenv("PIPER_DIR", "/models/piper")

    # El guion es texto fijo: cada frase se sintetiza una vez en la vida del
    # sistema y sobrevive a los reinicios.
    tts_cache_dir: str = os.getenv("TTS_CACHE_DIR", "/models/tts-cache")

    # --- Detección de fin de respuesta ---
    vad_aggressiveness: int = int(os.getenv("VAD_AGGRESSIVENESS", "2"))
    # Amplitud mínima (0-32767) para que una trama cuente como voz. Acompaña al
    # VAD, que por sí solo confunde ruido de sala con habla y deja la escucha
    # abierta hasta el tope. Subirlo si el bot no corta cuando el cliente
    # termina; bajarlo si corta a gente que habla bajito.
    speech_floor: int = int(os.getenv("SPEECH_FLOOR", "700"))
    silence_ms_to_stop: int = int(os.getenv("SILENCE_MS_TO_STOP", "1200"))
    max_answer_seconds: int = int(os.getenv("MAX_ANSWER_SECONDS", "30"))
    no_speech_timeout_seconds: int = int(os.getenv("NO_SPEECH_TIMEOUT_SECONDS", "8"))

    save_audio: bool = _env_bool("SAVE_AUDIO", True)
    recordings_dir: str = os.getenv("RECORDINGS_DIR", "/recordings")

    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def piper_model_path(self) -> str:
        return f"{self.piper_dir}/{self.piper_voice}.onnx"

    @property
    def piper_config_path(self) -> str:
        return f"{self.piper_dir}/{self.piper_voice}.onnx.json"

    @property
    def voicebox_enabled(self) -> bool:
        return bool(self.voicebox_url and self.voicebox_profile_id)

    @property
    def tts_engine(self) -> str:
        return "voicebox" if self.voicebox_enabled else "piper"


config = Config()
