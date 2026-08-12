"""Síntesis de voz con Piper (offline, sin API paga).

Piper genera a la frecuencia nativa de la voz (22050 Hz en las -high), así que
hay que resamplear a los 8 kHz que espera Asterisk.

El caché en memoria evita re-sintetizar la presentación y las preguntas en cada
llamada: son siempre el mismo texto y la síntesis cuesta ~200 ms.
"""

from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache

import numpy as np
import soxr

from app.config import SAMPLE_RATE, config

log = logging.getLogger(__name__)

_voice = None
_native_rate = 22050


def load_voice() -> None:
    """Carga el modelo una sola vez, al arrancar el servicio."""
    global _voice, _native_rate

    if _voice is not None:
        return

    from piper import PiperVoice

    model = config.piper_model_path
    if not os.path.exists(model):
        raise FileNotFoundError(
            f"No está el modelo de Piper en {model}.\n"
            f"Descargalo con: ./scripts/download_models.sh"
        )

    _voice = PiperVoice.load(model, config_path=config.piper_config_path)

    # La ruta del sample rate cambió entre versiones de piper-tts
    sample_rate = getattr(_voice, "config", None)
    _native_rate = getattr(sample_rate, "sample_rate", None) or 22050

    log.info(
        "Piper listo: voz=%s, %d Hz -> %d Hz",
        config.piper_voice, _native_rate, SAMPLE_RATE,
    )


def _synthesize_native(text: str) -> bytes:
    """PCM int16 a la frecuencia nativa de la voz.

    piper-tts cambió su API entre versiones: las nuevas exponen synthesize()
    devolviendo AudioChunk, las viejas synthesize_stream_raw() devolviendo bytes.
    Se soportan las dos para no quedar atado a una versión exacta.
    """
    if hasattr(_voice, "synthesize_stream_raw"):
        return b"".join(_voice.synthesize_stream_raw(text))

    chunks: list[bytes] = []
    for chunk in _voice.synthesize(text):
        # AudioChunk expone audio_int16_bytes; si no, es bytes directo
        chunks.append(getattr(chunk, "audio_int16_bytes", None) or bytes(chunk))
    return b"".join(chunks)


def _to_telephony(pcm_native: bytes) -> bytes:
    """Resamplea a 8 kHz mono int16, el formato de Asterisk."""
    if not pcm_native:
        return b""

    samples = np.frombuffer(pcm_native, dtype=np.int16)
    if _native_rate == SAMPLE_RATE:
        return samples.tobytes()

    resampled = soxr.resample(
        samples.astype(np.float32) / 32768.0, _native_rate, SAMPLE_RATE
    )
    return (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


@lru_cache(maxsize=256)
def _synthesize_cached(text: str) -> bytes:
    return _to_telephony(_synthesize_native(text))


async def synthesize(text: str) -> bytes:
    """PCM 8 kHz listo para mandar por AudioSocket.

    Piper es sincrónico: va a un thread para no bloquear el event loop, que
    está atendiendo otras llamadas en paralelo.
    """
    text = (text or "").strip()
    if not text:
        return b""

    return await asyncio.to_thread(_synthesize_cached, text)


def warm_up(texts: list[str]) -> None:
    """Pre-sintetiza frases fijas para que la primera llamada no pague el costo."""
    for text in texts:
        if text and text.strip():
            _synthesize_cached(text.strip())
    log.info("TTS precalentado con %d frases", len(texts))
