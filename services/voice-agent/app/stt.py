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
import re
import unicodedata
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

    _calentar()
    log.info("Whisper listo")


def _calentar() -> None:
    """Una transcripción de prueba al arrancar. En la GPU la primera inferencia prepara los kernels de CUDA y tarda
    unos 11 s (medido el 21/10/2026): sin esto, el primer cliente después de cada reinicio esperaba eso en silencio.
    Va sin el filtro de voz a propósito: con él, un audio sin voz se descarta y no se calienta nada."""
    import time

    inicio = time.monotonic()
    try:
        ruido = (np.random.default_rng(0).standard_normal(WHISPER_SAMPLE_RATE * 2) * 0.01).astype(np.float32)
        segmentos, _ = _model.transcribe(ruido, language=config.whisper_language, beam_size=5, vad_filter=False)
        list(segmentos)  # el decodificador corre recién al recorrer los segmentos
        log.info("Whisper calentado en %.1f s", time.monotonic() - inicio)
    except Exception as exc:  # noqa: BLE001 - calentar es una mejora: si falla, se atiende igual
        log.warning("No se pudo calentar Whisper (%s): la primera respuesta va a tardar más", exc)


def _pcm_to_float32(pcm: bytes) -> np.ndarray:
    """slin 8 kHz int16 -> float32 16 kHz normalizado, como espera Whisper."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if SAMPLE_RATE == WHISPER_SAMPLE_RATE:
        return samples
    return soxr.resample(samples, SAMPLE_RATE, WHISPER_SAMPLE_RATE)


PROMPT_BASE = (
    "Encuesta telefónica de satisfacción de un taller mecánico. "
    "El cliente califica con un número del cero al diez: "
    "cero, uno, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."
)


# Una respuesta más corta que esto es una palabra suelta («siete», «sí»): ahí la guía hace más daño que bien.
CORTO_SEGUNDOS = 2.5
# Menos que esto no es una palabra: un golpe, un clic. 0,15 s alcanza para un «sí» (medido: 0,17 s se entiende).
MIN_SEGUNDOS = 0.15
# Silencio que se le agrega a las palabras sueltas (< MARGEN_HASTA): con aire alrededor Whisper las entiende («7» →
# «Siete.», «» → «Diez.»). A una frase de 1 a 2 s le hace daño: «uno, un desastre» salió «¿Uno? ¿Unde está?».
MARGEN_SEGUNDOS = 0.3
MARGEN_HASTA = 1.0
# Lo que Whisper «oye» en el ruido y en los silencios (son subtítulos de los videos con que se entrenó).
ALUCINACIONES = (
    "gracias por ver el video", "gracias por ver", "suscribete", "subtitulos", "amara org", "no olvides suscribirte",
    "hasta la proxima", "nos vemos en el proximo",
)


def prompt_para(vocabulario: list[str] | None, corto: bool = False) -> str | None:
    """El prompt de Whisper: el sesgo hacia los números y, si hay, las palabras del agente (marcas, modelos,
    sucursales). Medido el 21/10/2026: con la lista escribió bien 17 de 17 nombres (sin ella, 8) y los números igual
    de bien. La API ya recorta la lista al tope del prompt (unos 220 tokens).

    Con una respuesta corta (`corto`) NO va el sesgo de los números ni la frase de la encuesta: en el audio real del
    22/10/2026 un «uno, un desastre» salió «uno, un, dos, tres, cuatro…» con la guía y perfecto sin ella (y «Diez.» salió
    «Encuesta telefónica…»). Solo las palabras del agente, que sí ayudan con los nombres."""
    palabras = [p.strip() for p in (vocabulario or []) if p and p.strip()]
    if corto:
        return f"Palabras que pueden aparecer: {', '.join(palabras)}." if palabras else None
    if not palabras:
        return PROMPT_BASE
    return f"{PROMPT_BASE} Palabras que pueden aparecer: {', '.join(palabras)}."


def es_alucinacion(texto: str) -> bool:
    """Whisper inventó una frase donde solo había ruido («¡Gracias por ver el vídeo!»)."""
    plano = " ".join(_palabras(texto))
    return bool(plano) and any(plano.startswith(a) or plano == a for a in ALUCINACIONES)


def _palabras(texto: str) -> list[str]:
    plano = unicodedata.normalize("NFKD", (texto or "").lower())
    return re.findall(r"[a-z0-9]+", "".join(c for c in plano if not unicodedata.combining(c)))


def es_eco_del_prompt(texto: str, prompt: str) -> bool:
    """Whisper devolvió su propio prompt en vez de lo que se dijo.

    Pasa con audios cortos: el cliente dijo «Ocho.» y salió «Encuesta telefónica de satisfacción de un taller
    mecánico.» (prueba e2e del 22/10/2026). Si casi todas las palabras están en el prompt y son varias, es eco: una
    respuesta de verdad («ocho», «cero, nunca») tiene una o dos palabras, o palabras que el prompt no tiene.
    """
    dichas = _palabras(texto)
    if len(dichas) < 4:
        return False
    del_prompt = set(_palabras(prompt))
    return sum(p in del_prompt for p in dichas) / len(dichas) >= 0.8


def _transcribe_sync(pcm: bytes, vocabulario: list[str] | None = None) -> Transcription:
    duracion = len(pcm) / (SAMPLE_RATE * 2)
    corto = duracion < CORTO_SEGUNDOS
    margen = duracion < MARGEN_HASTA
    prompt = prompt_para(vocabulario, corto)
    resultado = _transcribir(pcm, prompt, margen=margen)
    if prompt and es_eco_del_prompt(resultado.text, prompt):
        log.info("Whisper devolvió el prompt (%r): se transcribe de nuevo sin él", resultado.text[:60])
        resultado = _transcribir(pcm, None, margen=margen)
    if es_alucinacion(resultado.text):
        log.info("Whisper inventó una frase donde había ruido (%r): se descarta", resultado.text[:60])
        resultado = Transcription(text="", confidence=None, duration=resultado.duration)
    return resultado


def _transcribir(pcm: bytes, prompt: str | None, margen: bool = False) -> Transcription:
    duration = len(pcm) / (SAMPLE_RATE * 2)
    audio = _pcm_to_float32(pcm)
    if margen:
        aire = np.zeros(int(WHISPER_SAMPLE_RATE * MARGEN_SEGUNDOS), dtype=np.float32)
        audio = np.concatenate([aire, audio, aire])

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
        initial_prompt=prompt,
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


async def transcribe(pcm: bytes, vocabulario: list[str] | None = None) -> Transcription:
    """Transcribe en un thread aparte: el modelo bloquea el event loop."""
    if len(pcm) < MIN_SEGUNDOS * SAMPLE_RATE * 2:  # un golpe, no una palabra: no hay nada que transcribir
        return Transcription(text="", confidence=None, duration=len(pcm) / (SAMPLE_RATE * 2))

    return await asyncio.to_thread(_transcribe_sync, pcm, vocabulario)
