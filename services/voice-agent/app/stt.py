"""Transcripción con faster-whisper.

El audio llega a 8 kHz (telefonía) y Whisper trabaja a 16 kHz, así que se
resamplea antes. Subir de 8k a 16k no agrega información, pero es lo que el
modelo espera y funciona bien: Whisper fue entrenado con audio telefónico entre
otras fuentes.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

import numpy as np
import soxr

from app.config import SAMPLE_RATE, WHISPER_SAMPLE_RATE, config

log = logging.getLogger(__name__)

_model = None


@dataclass
class Transcription:
    text: str
    confidence: float | None
    duration: float


def load_model() -> None:
    """Carga el modelo una vez. En GPU tarda unos segundos."""
    global _model

    if _model is not None:
        return

    from faster_whisper import WhisperModel

    log.info(
        "Cargando Whisper %s en %s (%s)...",
        config.whisper_model, config.whisper_device, config.whisper_compute_type,
    )

    try:
        _model = WhisperModel(
            config.whisper_model,
            device=config.whisper_device,
            compute_type=config.whisper_compute_type,
            download_root="/models/whisper",
        )
    except Exception as exc:  # noqa: BLE001
        # Falla típica: no hay GPU visible o falta cuDNN. Mejor degradar a CPU
        # que dejar el servicio caído y perder todas las llamadas.
        if config.whisper_device != "cpu":
            log.error("Whisper no arrancó en %s (%s). Cayendo a CPU.", config.whisper_device, exc)
            _model = WhisperModel(
                config.whisper_model,
                device="cpu",
                compute_type="int8",
                download_root="/models/whisper",
            )
        else:
            raise

    log.info("Whisper listo")


def _pcm_to_float32(pcm: bytes) -> np.ndarray:
    """slin 8 kHz int16 -> float32 16 kHz normalizado, como espera Whisper."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if SAMPLE_RATE == WHISPER_SAMPLE_RATE:
        return samples
    return soxr.resample(samples, SAMPLE_RATE, WHISPER_SAMPLE_RATE)


def _transcribe_sync(pcm: bytes) -> Transcription:
    duration = len(pcm) / (SAMPLE_RATE * 2)
    audio = _pcm_to_float32(pcm)

    segments, info = _model.transcribe(
        audio,
        language=config.whisper_language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        # Sin contexto previo: cada respuesta es independiente y arrastrar el
        # texto anterior hace que Whisper invente continuaciones.
        condition_on_previous_text=False,
        # Sesgo hacia el dominio: mejora el reconocimiento de los números
        # hablados. Tiene que nombrar el rango real (0-10): si dice "uno al
        # cinco", Whisper transcribe "nueve" y "diez" mucho peor, y son
        # justamente los valores que definen si el cliente quedó conforme.
        initial_prompt=(
            "Encuesta telefónica de satisfacción de un taller mecánico. "
            "El cliente califica con un número del cero al diez: "
            "cero, uno, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."
        ),
    )

    parts: list[str] = []
    logprobs: list[float] = []

    for segment in segments:
        parts.append(segment.text.strip())
        if segment.avg_logprob is not None:
            logprobs.append(segment.avg_logprob)

    text = " ".join(p for p in parts if p).strip()

    # avg_logprob es log-probabilidad; exp() lo lleva a un 0-1 interpretable
    confidence = None
    if logprobs:
        confidence = round(min(1.0, math.exp(sum(logprobs) / len(logprobs))), 3)

    log.debug("Transcripción (%.1fs, conf=%s): %r", duration, confidence, text)
    return Transcription(text=text, confidence=confidence, duration=duration)


async def transcribe(pcm: bytes) -> Transcription:
    """Transcribe en un thread aparte: el modelo bloquea el event loop."""
    if len(pcm) < SAMPLE_RATE:  # menos de 0,5 s: no hay nada que transcribir
        return Transcription(text="", confidence=None, duration=len(pcm) / (SAMPLE_RATE * 2))

    return await asyncio.to_thread(_transcribe_sync, pcm)
