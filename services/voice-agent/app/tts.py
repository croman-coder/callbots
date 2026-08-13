"""Síntesis de voz: Voicebox (voz clonada) con Piper de respaldo.

El guion de una encuesta es **texto fijo** — presentación, preguntas, cierre.
Siempre el mismo. Por eso la calidad de voz puede costar lo que sea: cada frase
se sintetiza una vez, se guarda en disco a 8 kHz y se reutiliza para siempre.

    ┌─ memoria ──► hit: microsegundos
    ├─ disco ────► hit: milisegundos (sobrevive reinicios)
    ├─ voicebox ─► miss: segundos (voz clonada)
    └─ piper ────► voicebox caído o lento: milisegundos (voz genérica)

El respaldo a Piper no es opcional: si voicebox se cae a mitad de una campaña,
una encuesta con voz robótica sigue siendo una encuesta. Silencio no.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import wave

import httpx
import numpy as np
import soxr

from app.config import SAMPLE_RATE, config

log = logging.getLogger(__name__)

_voice = None                 # modelo de Piper, se carga perezoso
_piper_rate = 22050

# ponytail: caché en memoria sin tope. El guion son ~10 frases fijas, pero cada
# {nombre} distinto suma una entrada (~80 KB). Con miles de nombres únicos entre
# reinicios conviene un LRU; hasta entonces, un dict es lo correcto.
_memory_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Conversión a formato de telefonía
# ---------------------------------------------------------------------------
def _to_telephony(samples: np.ndarray, source_rate: int) -> bytes:
    """int16 a cualquier frecuencia -> PCM 8 kHz mono int16 (lo que pide Asterisk)."""
    if samples.size == 0:
        return b""

    if source_rate == SAMPLE_RATE:
        return samples.astype(np.int16).tobytes()

    resampled = soxr.resample(
        samples.astype(np.float32) / 32768.0, source_rate, SAMPLE_RATE
    )
    return (np.clip(resampled, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _wav_to_telephony(wav_bytes: bytes) -> bytes:
    """Convierte el WAV que devuelve voicebox. Usa el módulo `wave` de stdlib."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if width != 2:
        raise ValueError(f"WAV de {width * 8} bits; se esperaba 16")

    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        # Mezcla a mono promediando canales; int32 evita el overflow al sumar
        samples = samples.reshape(-1, channels).astype(np.int32).mean(axis=1).astype(np.int16)

    return _to_telephony(samples, rate)


# ---------------------------------------------------------------------------
# Caché en disco
# ---------------------------------------------------------------------------
def _cache_key(text: str) -> str:
    """La clave incluye motor y perfil: cambiar de voz invalida el caché solo."""
    seed = f"{config.tts_engine}|{config.voicebox_profile_id}|{config.piper_voice}|{text}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _cache_path(text: str) -> str:
    return os.path.join(config.tts_cache_dir, f"{_cache_key(text)}.pcm")


def _read_cache(text: str) -> bytes | None:
    path = _cache_path(text)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _write_cache(text: str, pcm: bytes) -> None:
    if not pcm:
        return
    path = _cache_path(text)
    try:
        os.makedirs(config.tts_cache_dir, exist_ok=True)
        # Escritura atómica: un contenedor que muere a mitad no deja un .pcm
        # truncado que después se reproduzca como audio cortado.
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as handle:
            handle.write(pcm)
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("No se pudo cachear el audio en %s: %s", path, exc)


# ---------------------------------------------------------------------------
# Motor 1: Voicebox (voz clonada)
# ---------------------------------------------------------------------------
async def _voicebox_synthesize(text: str, timeout: float) -> bytes:
    """POST /generate/stream devuelve el WAV directo, sin pasar por disco."""
    payload = {
        "profile_id": config.voicebox_profile_id,
        "text": text,
        "language": config.voicebox_language,
        "engine": config.voicebox_engine,
        "normalize": True,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.voicebox_url.rstrip('/')}/generate/stream", json=payload
        )
        response.raise_for_status()
        return _wav_to_telephony(response.content)


# ---------------------------------------------------------------------------
# Motor 2: Piper (respaldo)
# ---------------------------------------------------------------------------
def load_voice() -> None:
    """Carga Piper. Con voicebox activo es solo el respaldo, así que no es fatal."""
    global _voice, _piper_rate

    if _voice is not None:
        return

    if not os.path.exists(config.piper_model_path):
        message = f"No está el modelo de Piper en {config.piper_model_path}"
        if config.voicebox_enabled:
            log.warning("%s. Sin respaldo si voicebox falla.", message)
            return
        raise FileNotFoundError(f"{message}\nDescargalo con: ./scripts/download_models.sh")

    from piper import PiperVoice

    _voice = PiperVoice.load(config.piper_model_path, config_path=config.piper_config_path)
    _piper_rate = getattr(getattr(_voice, "config", None), "sample_rate", None) or 22050
    log.info("Piper listo: %s (%d Hz)", config.piper_voice, _piper_rate)


def _piper_synthesize(text: str) -> bytes:
    """piper-tts cambió de API entre versiones; se soportan las dos formas."""
    if _voice is None:
        return b""

    if hasattr(_voice, "synthesize_stream_raw"):
        raw = b"".join(_voice.synthesize_stream_raw(text))
    else:
        chunks = [
            getattr(chunk, "audio_int16_bytes", None) or bytes(chunk)
            for chunk in _voice.synthesize(text)
        ]
        raw = b"".join(chunks)

    return _to_telephony(np.frombuffer(raw, dtype=np.int16), _piper_rate)


# ---------------------------------------------------------------------------
# Interfaz pública
# ---------------------------------------------------------------------------
async def synthesize(text: str, timeout: float | None = None) -> bytes:
    """PCM 8 kHz listo para AudioSocket. Nunca levanta excepción: peor caso, b''."""
    text = (text or "").strip()
    if not text:
        return b""

    if text in _memory_cache:
        return _memory_cache[text]

    cached = _read_cache(text)
    if cached is not None:
        _memory_cache[text] = cached
        return cached

    pcm = b""
    if config.voicebox_enabled:
        try:
            pcm = await _voicebox_synthesize(
                text, timeout or config.voicebox_call_timeout
            )
        except (httpx.HTTPError, ValueError, wave.Error) as exc:
            log.warning("Voicebox no pudo sintetizar %r: %s", text[:50], exc)

    if not pcm:
        # Piper es sincrónico: a un thread, que el event loop atiende otras llamadas
        pcm = await asyncio.to_thread(_piper_synthesize, text)
        if pcm and config.voicebox_enabled:
            # Respaldo: no se cachea. Si se guardara, la primera vez que voicebox
            # esté caído esa frase quedaría con voz de Piper para siempre.
            _memory_cache[text] = pcm
            return pcm

    if not pcm:
        log.error("Ningún motor pudo sintetizar %r", text[:50])
        return b""

    _memory_cache[text] = pcm
    _write_cache(text, pcm)
    return pcm


async def warm_up(texts: list[str]) -> int:
    """Pre-sintetiza frases fijas. Devuelve cuántas se generaron nuevas.

    Se corre al arrancar el servicio, no durante una llamada: con voicebox cada
    frase puede tardar decenas de segundos y del otro lado habría un cliente
    escuchando silencio.
    """
    pending = [
        t.strip() for t in dict.fromkeys(texts)
        if t and t.strip() and t.strip() not in _memory_cache
    ]
    if not pending:
        return 0

    generated = 0
    for text in pending:
        if _read_cache(text) is not None:
            continue

        await synthesize(text, timeout=config.voicebox_warmup_timeout)

        # Solo cuenta si quedó en disco. Si respondió Piper de respaldo no se
        # cachea a propósito, y decir "precalentado" ahí sería mentirle al log.
        if _read_cache(text) is not None:
            generated += 1
            log.info("Precalentado (%d/%d): %r", generated, len(pending), text[:60])
        else:
            log.warning("No quedó cacheado: %r", text[:60])

    return generated
