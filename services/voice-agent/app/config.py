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
    # Con Kokoro una frase sale en medio segundo: en llamada se la espera hasta este tope antes de
    # caer a Piper, que suena robótico y con otra voz. Los motores de clonación no se esperan.
    voicebox_vivo_timeout: float = float(os.getenv("VOICEBOX_VIVO_TIMEOUT", "3"))

    # VoiceStudio (OmniVoice): voces clonadas que hablan en vivo, pero solo con GPU (en CPU una
    # frase tarda ~25 s). Las campañas lo eligen con un perfil "vs:<id>".
    voicestudio_url: str = os.getenv("VOICESTUDIO_URL", "")
    voicestudio_api_key: str = os.getenv("VOICESTUDIO_API_KEY", "")
    voicestudio_language: str = os.getenv("VOICESTUDIO_LANGUAGE", "Spanish")
    voicestudio_vivo_timeout: float = float(os.getenv("VOICESTUDIO_VIVO_TIMEOUT", "4"))
    # Pasos de difusión: menos es más rápido y algo menos fino. Medido en la notebook (RTX 5060), una
    # frase de 6 s: 8 pasos 0,8 s; 12 pasos 1,1 s; 16 pasos 1,4 s; 32 pasos 2,6 s.
    voicestudio_pasos: int = int(os.getenv("VOICESTUDIO_PASOS", "12"))

    # ElevenLabs (voces de la cuenta de Santa Rosa; de pago, por carácter). Las campañas lo usan con un
    # perfil "el:<voice_id>". Sin clave no se ofrece.
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    elevenlabs_url: str = os.getenv("ELEVENLABS_URL", "https://api.elevenlabs.io")
    # multilingual_v2: la más natural. flash_v2_5: casi sin demora y la mitad de precio, algo menos fina.
    elevenlabs_model: str = os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2")
    elevenlabs_vivo_timeout: float = float(os.getenv("ELEVENLABS_VIVO_TIMEOUT", "6"))
    elevenlabs_estabilidad: float = float(os.getenv("ELEVENLABS_ESTABILIDAD", "0.5"))
    elevenlabs_similitud: float = float(os.getenv("ELEVENLABS_SIMILITUD", "0.8"))
    # Freno contra gastos por error (un guion mal armado, un bucle): al pasar este total de caracteres en
    # el día, las frases nuevas salen con la voz del servidor. Lo que ya está guardado sigue sonando igual.
    elevenlabs_max_caracteres_dia: int = int(os.getenv("ELEVENLABS_MAX_CARACTERES_DIA", "60000"))

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
