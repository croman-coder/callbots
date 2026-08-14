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
from dataclasses import dataclass

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
# La clave lleva el texto y la modulación: la misma frase con otra voz
# es otro audio.
_memory_cache: dict[tuple[str, str], bytes] = {}


# ---------------------------------------------------------------------------
# Modulación de la voz
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VoiceParams:
    """Cómo suena el bot. Vienen de la campaña, en el guion de cada llamada.

    Los defaults son los de Piper: una campaña sin tocar suena igual que
    antes de que esto existiera.
    """

    speed: float = 1.0            # 1 = normal, >1 más rápido
    pitch: float = 1.0            # 1 = normal, >1 más agudo
    expressiveness: float = 0.667  # noise_scale de Piper
    volume: float = 1.0

    def clave(self) -> str:
        return f"{self.speed:.3f}/{self.pitch:.3f}/{self.expressiveness:.3f}/{self.volume:.3f}"


DEFAULT_PARAMS = VoiceParams()


def _to_telephony(
    samples: np.ndarray, source_rate: int, pitch: float = 1.0
) -> bytes:
    """int16 a cualquier frecuencia -> PCM 8 kHz mono int16 (lo que pide Asterisk).

    `pitch` desplaza el tono resampleando: si en vez de a 8000 Hz se resamplea
    a 8000/pitch y después se reproduce a 8000, la voz sale `pitch` veces más
    aguda y más corta. La duración la devuelve el sintetizador, que genera el
    audio proporcionalmente más largo (ver _piper_synthesize).
    """
    if samples.size == 0:
        return b""

    destino = SAMPLE_RATE / max(pitch, 0.01)

    if source_rate == destino:
        return samples.astype(np.int16).tobytes()

    resampled = soxr.resample(
        samples.astype(np.float32) / 32768.0, source_rate, destino
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
def _cache_key(text: str, params: VoiceParams) -> str:
    """Incluye motor, perfil y modulación: cambiar cualquiera invalida el caché.

    Sin los parámetros acá, subir la velocidad en el panel seguiría
    reproduciendo el audio viejo hasta que alguien borre el caché a mano.
    """
    seed = (
        f"{config.tts_engine}|{config.voicebox_profile_id}|{config.piper_voice}"
        f"|{params.clave()}|{text}"
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def _cache_path(text: str, params: VoiceParams) -> str:
    return os.path.join(config.tts_cache_dir, f"{_cache_key(text, params)}.pcm")


def _read_cache(text: str, params: VoiceParams) -> bytes | None:
    path = _cache_path(text, params)
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _write_cache(text: str, pcm: bytes, params: VoiceParams) -> None:
    if not pcm:
        return
    path = _cache_path(text, params)
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


def _synthesis_config(params: VoiceParams):
    """SynthesisConfig de Piper, o None si la versión instalada no lo tiene."""
    try:
        from piper import SynthesisConfig
    except ImportError:
        return None

    # length_scale es lo inverso de la velocidad. Se multiplica por el tono
    # porque el desplazamiento de tono acorta el audio en la misma
    # proporción: generándolo más largo, la duración final queda igual.
    length_scale = (1.0 / max(params.speed, 0.1)) * max(params.pitch, 0.1)

    return SynthesisConfig(
        length_scale=length_scale,
        noise_scale=params.expressiveness,
        # La variación de duración de los fonemas acompaña a la
        # expresividad, si no la voz suena expresiva pero métricamente rígida.
        noise_w_scale=params.expressiveness * 1.2,
        volume=1.0,  # el volumen se aplica abajo, así vale en las dos APIs
    )


def _piper_synthesize(text: str, params: VoiceParams = DEFAULT_PARAMS) -> bytes:
    """piper-tts cambió de API entre versiones; se soportan las dos formas."""
    if _voice is None:
        return b""

    if hasattr(_voice, "synthesize_stream_raw"):
        # API vieja: no acepta configuración, solo se puede modular el tono y
        # el volumen, que se aplican sobre la onda ya generada.
        raw = b"".join(_voice.synthesize_stream_raw(text))
    else:
        syn = _synthesis_config(params)
        chunks = [
            getattr(chunk, "audio_int16_bytes", None) or bytes(chunk)
            for chunk in (
                _voice.synthesize(text, syn_config=syn)
                if syn is not None
                else _voice.synthesize(text)
            )
        ]
        raw = b"".join(chunks)

    samples = np.frombuffer(raw, dtype=np.int16)

    if params.volume != 1.0 and samples.size:
        # En float para no envolver el int16 al saturar: un clip suena feo
        # pero un wrap suena a explosión.
        escalado = samples.astype(np.float32) * params.volume
        samples = np.clip(escalado, -32768, 32767).astype(np.int16)

    return _to_telephony(samples, _piper_rate, params.pitch)


# ---------------------------------------------------------------------------
# Interfaz pública
# ---------------------------------------------------------------------------
async def synthesize(
    text: str,
    timeout: float | None = None,
    params: VoiceParams = DEFAULT_PARAMS,
) -> bytes:
    """PCM 8 kHz listo para AudioSocket. Nunca levanta excepción: peor caso, b''."""
    text = (text or "").strip()
    if not text:
        return b""

    clave_memoria = (text, params.clave())
    if clave_memoria in _memory_cache:
        return _memory_cache[clave_memoria]

    cached = _read_cache(text, params)
    if cached is not None:
        _memory_cache[clave_memoria] = cached
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
        pcm = await asyncio.to_thread(_piper_synthesize, text, params)
        if pcm and config.voicebox_enabled:
            # Respaldo: no se cachea. Si se guardara, la primera vez que voicebox
            # esté caído esa frase quedaría con voz de Piper para siempre.
            _memory_cache[clave_memoria] = pcm
            return pcm

    if not pcm:
        log.error("Ningún motor pudo sintetizar %r", text[:50])
        return b""

    _memory_cache[clave_memoria] = pcm
    _write_cache(text, pcm, params)
    return pcm


async def warm_up(texts: list[str], params: VoiceParams = DEFAULT_PARAMS) -> int:
    """Pre-sintetiza frases fijas. Devuelve cuántas se generaron nuevas.

    Se corre al arrancar el servicio, no durante una llamada: con voicebox cada
    frase puede tardar decenas de segundos y del otro lado habría un cliente
    escuchando silencio.
    """
    pending = [
        t.strip() for t in dict.fromkeys(texts)
        if t and t.strip() and (t.strip(), params.clave()) not in _memory_cache
    ]
    if not pending:
        return 0

    generated = 0
    for text in pending:
        if _read_cache(text, params) is not None:
            continue

        await synthesize(text, timeout=config.voicebox_warmup_timeout, params=params)

        # Solo cuenta si quedó en disco. Si respondió Piper de respaldo no se
        # cachea a propósito, y decir "precalentado" ahí sería mentirle al log.
        if _read_cache(text, params) is not None:
            generated += 1
            log.info("Precalentado (%d/%d): %r", generated, len(pending), text[:60])
        else:
            log.warning("No quedó cacheado: %r", text[:60])

    return generated
