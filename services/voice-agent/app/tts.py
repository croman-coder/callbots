"""Síntesis de voz: Voicebox (voz clonada) con Piper de respaldo.

El guion de una encuesta es **texto fijo** — presentación, preguntas, cierre.
Siempre el mismo. Por eso la calidad de voz puede costar lo que sea: cada frase
se sintetiza una vez, se guarda en disco a 8 kHz y se reutiliza para siempre.

    ┌─ memoria ──► hit: microsegundos
    ├─ disco ────► hit: milisegundos (sobrevive reinicios)
    ├─ voicestudio ► miss: ~1 s con GPU (voz clonada, en vivo; perfiles "vs:<id>")
    ├─ elevenlabs ─► miss: ~0,5 s, de pago por carácter (voces de la cuenta; perfiles "el:<id>")
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
import re
import wave
from dataclasses import dataclass, replace
from datetime import datetime

import httpx
import numpy as np
import soxr

from app.config import SAMPLE_RATE, config
from app.guion import partir_frases

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
    # Qué voz: "" = la del servidor (VOICEBOX_PROFILE_ID, o Piper si no hay), "piper" = Piper
    # a propósito, otro valor = el id de un perfil de Voicebox. Es de la campaña.
    profile_id: str = ""

    def clave(self) -> str:
        # La voz va en la clave: dos campañas con la misma modulación y distinta voz no pueden
        # compartir el audio en memoria.
        return (
            f"{self.speed:.3f}/{self.pitch:.3f}/{self.expressiveness:.3f}/{self.volume:.3f}"
            f"/{self.profile_id}"
        )


PREFIJO_VOICESTUDIO = "vs:"
PREFIJO_ELEVENLABS = "el:"
_ID_ELEVENLABS = re.compile(r"[A-Za-z0-9]{10,40}")


def perfil_de_elevenlabs(params: "VoiceParams") -> str | None:
    """El id de voz de ElevenLabs que habla en esta campaña (sin el prefijo), o None."""
    if config.elevenlabs_api_key and params.profile_id.startswith(PREFIJO_ELEVENLABS):
        voz = params.profile_id[len(PREFIJO_ELEVENLABS):]
        return voz if _ID_ELEVENLABS.fullmatch(voz) else None
    return None


def perfil_de_voicestudio(params: "VoiceParams") -> str | None:
    """El perfil de VoiceStudio que habla en esta campaña (sin el prefijo), o None."""
    if config.voicestudio_url and params.profile_id.startswith(PREFIJO_VOICESTUDIO):
        return params.profile_id[len(PREFIJO_VOICESTUDIO):] or None
    return None


def perfil_de_voicebox(params: "VoiceParams") -> str | None:
    """El perfil de Voicebox que habla en esta campaña, o None si habla Piper."""
    if not config.voicebox_url or params.profile_id == "piper":
        return None
    if params.profile_id.startswith(PREFIJO_VOICESTUDIO):
        # Es de VoiceStudio: Voicebox no lo conoce. Si VoiceStudio no está, habla la voz del servidor.
        return None if config.voicestudio_url else (config.voicebox_profile_id or None)
    if params.profile_id.startswith(PREFIJO_ELEVENLABS):
        # Igual con ElevenLabs: sin clave (o con un id raro) habla la voz del servidor.
        return None if perfil_de_elevenlabs(params) else (config.voicebox_profile_id or None)
    return params.profile_id or config.voicebox_profile_id or None


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
    perfil = perfil_de_voicebox(params)
    estudio = perfil_de_voicestudio(params)
    eleven = perfil_de_elevenlabs(params)
    if estudio:
        motor = f"voicestudio:{estudio}:{config.voicestudio_pasos}"
    elif eleven:
        motor = f"elevenlabs:{eleven}:{config.elevenlabs_model}:{config.elevenlabs_estabilidad}:{config.elevenlabs_similitud}"
    else:
        motor = f"voicebox:{perfil}:{config.voicebox_engine}" if perfil else "piper"
    seed = f"{motor}|{config.piper_voice}|{params.clave()}|{text}"
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
_engines: dict[str, str] = {}


async def _engine_de(perfil: str) -> str:
    """Con qué motor habla un perfil: una voz preset solo funciona con el suyo (Kokoro, etc.) y
    Voicebox rechaza cualquier otro; una clonada usa el de clonación. Se pregunta una vez por perfil."""
    if perfil in _engines:
        return _engines[perfil]
    engine = config.voicebox_engine
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{config.voicebox_url.rstrip('/')}/profiles/{perfil}")
            r.raise_for_status()
            datos = r.json()
        if datos.get("voice_type") == "preset" and datos.get("preset_engine"):
            engine = str(datos["preset_engine"])
        else:
            engine = str(datos.get("default_engine") or engine)
        _engines[perfil] = engine  # solo si se pudo leer: un fallo pasajero no fija el motor equivocado
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("No se pudo leer el perfil %s de Voicebox: %s", perfil[:8], exc)
    return engine


async def _voicebox_synthesize(text: str, timeout: float, perfil: str) -> bytes:
    """POST /generate/stream devuelve el WAV directo, sin pasar por disco."""
    payload = {
        "profile_id": perfil,
        "text": text,
        "language": config.voicebox_language,
        "engine": await _engine_de(perfil),
        "normalize": True,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.voicebox_url.rstrip('/')}/generate/stream", json=payload
        )
        response.raise_for_status()
        return _wav_to_telephony(response.content)


# ---------------------------------------------------------------------------
# Motor 1b: VoiceStudio (voz clonada en vivo, con GPU)
# ---------------------------------------------------------------------------
async def _voicestudio_synthesize(text: str, timeout: float, perfil: str, speed: float = 1.0) -> bytes:
    """POST /v1/audio/speech (compatible con OpenAI): `voice` es el id del perfil clonado."""
    payload = {
        "model": "omnivoice",
        "input": text,
        "voice": perfil,
        "response_format": "wav",
        "language": config.voicestudio_language,
        "speed": min(max(speed, 0.5), 2.0),
        "num_step": config.voicestudio_pasos,
    }
    cabeceras = {"Authorization": f"Bearer {config.voicestudio_api_key}"} if config.voicestudio_api_key else {}
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.voicestudio_url.rstrip('/')}/v1/audio/speech", json=payload, headers=cabeceras
        )
        response.raise_for_status()
        return _wav_to_telephony(response.content)


# ---------------------------------------------------------------------------
# Motor 1c: ElevenLabs (voces de la cuenta; de pago por carácter)
# ---------------------------------------------------------------------------
_gasto_eleven = {"dia": "", "caracteres": 0, "avisado": False}


def _hay_presupuesto_elevenlabs(caracteres: int) -> bool:
    """Suma los caracteres pedidos hoy y dice si todavía entran en el tope diario.

    Se cuenta antes de pedir: es plata. La cuenta es en memoria y se reinicia con el día (hora del servidor);
    si el servicio se reinicia a mitad del día, el tope vuelve a empezar, y el límite de verdad lo tiene
    la propia cuenta de ElevenLabs.
    """
    hoy = datetime.now().strftime("%Y-%m-%d")
    if _gasto_eleven["dia"] != hoy:
        _gasto_eleven.update(dia=hoy, caracteres=0, avisado=False)
    if _gasto_eleven["caracteres"] + caracteres > config.elevenlabs_max_caracteres_dia:
        if not _gasto_eleven["avisado"]:
            _gasto_eleven["avisado"] = True
            log.error(
                "ElevenLabs: tope diario de %d caracteres alcanzado. Las frases nuevas salen con la voz del "
                "servidor hasta mañana (se sube con ELEVENLABS_MAX_CARACTERES_DIA).",
                config.elevenlabs_max_caracteres_dia,
            )
        return False
    _gasto_eleven["caracteres"] += caracteres
    return True


async def _elevenlabs_synthesize(text: str, timeout: float, voz: str, speed: float = 1.0) -> bytes:
    """POST /v1/text-to-speech/<voz> con salida PCM de 16 kHz, que se baja a 8 kHz para la línea."""
    payload = {
        "text": text,
        "model_id": config.elevenlabs_model,
        "voice_settings": {
            "stability": config.elevenlabs_estabilidad,
            "similarity_boost": config.elevenlabs_similitud,
            "use_speaker_boost": True,
            # ElevenLabs acepta 0,7 a 1,2.
            "speed": min(max(speed, 0.7), 1.2),
        },
    }
    if "v2_5" in config.elevenlabs_model:
        payload["language_code"] = "es"  # solo los modelos v2.5 permiten fijar el idioma
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            f"{config.elevenlabs_url.rstrip('/')}/v1/text-to-speech/{voz}",
            params={"output_format": "pcm_16000"},
            json=payload,
            headers={"xi-api-key": config.elevenlabs_api_key, "Accept": "audio/pcm"},
        )
        response.raise_for_status()
        crudo = response.content
    if len(crudo) < 3200:  # menos de 0,1 s: no es audio, es un error disfrazado
        raise ValueError("ElevenLabs devolvió un audio vacío")
    return _to_telephony(np.frombuffer(crudo[: len(crudo) // 2 * 2], dtype=np.int16), 16000)


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
        if config.voicebox_url:
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
PAUSA_ENTRE_FRASES_MS = 450
# Motores que generan más rápido que lo que dura el audio: se pueden usar en vivo.
MOTORES_RAPIDOS = {"kokoro"}


def _silencio(ms: int) -> bytes:
    """PCM 8 kHz mono int16 en silencio."""
    return b"\x00" * (SAMPLE_RATE * 2 * ms // 1000)


async def synthesize(
    text: str,
    timeout: float | None = None,
    params: VoiceParams = DEFAULT_PARAMS,
) -> bytes:
    """PCM 8 kHz listo para AudioSocket. Nunca levanta excepción: peor caso, b''.

    Un texto con varias frases se sintetiza frase por frase y se une con una pausa: dicho de
    corrido suena a locutor sin respirar. Cada frase se cachea por separado, así el saludo
    comparte audio con otros textos y `warm_up` solo genera lo que falta.
    """
    frases = partir_frases(text)
    if len(frases) <= 1:
        return await _sintetizar_frase(frases[0] if frases else "", timeout, params)
    partes = []
    for frase in frases:
        pcm = await _sintetizar_frase(frase, timeout, params)
        if pcm:
            partes.append(pcm)
    return _silencio(PAUSA_ENTRE_FRASES_MS).join(partes)


async def sintetizar_frase(text: str, params: VoiceParams = DEFAULT_PARAMS) -> bytes:
    """Una sola frase, en llamada. El diálogo la usa para preparar la siguiente mientras suena esta."""
    return await _sintetizar_frase(text, None, params)


# Volumen percibido de destino para el teléfono (≈ -19 dBFS de RMS sobre la voz, sin contar silencios).
NIVEL_RMS = 0.11
TECHO = 0.9


def nivelar(pcm: bytes, volumen: float = 1.0) -> bytes:
    """Deja cada frase al mismo volumen percibido, fuerte y sin saturar.

    Voicebox normaliza el pico, no lo que se oye: una frase suave salía más baja que la anterior y
    en un celular se perdía. Se mide la energía solo de las tramas con voz (las pausas no cuentan),
    se lleva a NIVEL_RMS × volumen y, si algún pico pasa el techo, se baja todo un poco. Aplicarla dos
    veces da lo mismo, así que el audio cacheado antes de este cambio también queda parejo.
    """
    if not pcm:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    n = (x.size // 160) * 160
    if n == 0:
        return pcm
    tramas = x[:n].reshape(-1, 160)
    energia = np.sqrt(np.mean(tramas**2, axis=1))
    voz = energia[energia > 0.01]
    if voz.size == 0:
        return pcm  # silencio o casi: no hay nada que nivelar
    rms = float(np.sqrt(np.mean(voz**2)))
    y = x * min(NIVEL_RMS * max(volumen, 0.1) / rms, 6.0)
    pico = float(np.max(np.abs(y)))
    if pico > TECHO:
        y *= TECHO / pico
    return (np.clip(y, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


async def _sintetizar_frase(
    text: str,
    timeout: float | None,
    params: VoiceParams,
) -> bytes:
    return nivelar(await _sintetizar_crudo(text, timeout, params), params.volume)


async def _sintetizar_crudo(
    text: str,
    timeout: float | None,
    params: VoiceParams,
) -> bytes:
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
    estudio = perfil_de_voicestudio(params)
    if estudio:
        # Con GPU la frase sale en ~1 s: se la espera, en llamada y al precalentar. Si VoiceStudio
        # no contesta, habla la voz del servidor sin cachear con esta clave: si se guardara, la frase
        # quedaría para siempre con otra voz.
        espera = timeout or config.voicestudio_vivo_timeout
        try:
            pcm = await asyncio.wait_for(
                _voicestudio_synthesize(text, espera, estudio, params.speed), espera
            )
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError, wave.Error) as exc:
            log.warning("VoiceStudio no llegó a tiempo para %r (%s): habla la voz del servidor", text[:40], exc or "tiempo")
        if not pcm:
            return await _sintetizar_crudo(text, timeout, replace(params, profile_id=""))
        _memory_cache[clave_memoria] = pcm
        _write_cache(text, pcm, params)
        return pcm

    eleven = perfil_de_elevenlabs(params)
    if eleven:
        # Se espera (con tope), en llamada y al precalentar, como con VoiceStudio. Si falla o no hay
        # presupuesto, habla la voz del servidor SIN cachear con esta clave: se volvería a probar la
        # próxima vez y la frase no queda guardada con otra voz para siempre.
        espera = timeout or config.elevenlabs_vivo_timeout
        try:
            if not _hay_presupuesto_elevenlabs(len(text)):
                raise ValueError("tope diario de caracteres")
            pcm = await asyncio.wait_for(_elevenlabs_synthesize(text, espera, eleven, params.speed), espera)
        except (asyncio.TimeoutError, httpx.HTTPError, ValueError) as exc:
            log.warning("ElevenLabs no pudo con %r (%s): habla la voz del servidor", text[:40], exc or "tiempo")
        if not pcm:
            return await _sintetizar_crudo(text, timeout, replace(params, profile_id=""))
        _memory_cache[clave_memoria] = pcm
        _write_cache(text, pcm, params)
        return pcm

    perfil = perfil_de_voicebox(params)
    # En llamada (sin timeout explícito) hay un cliente escuchando. Con Kokoro la frase sale en
    # medio segundo: se la espera (con tope) y así nunca cambia la voz en mitad de la charla. Los
    # motores de clonación tardan decenas de segundos en CPU: ahí habla Piper al instante y la voz
    # buena se genera en segundo plano para la próxima.
    if perfil and timeout is None:
        if await _engine_de(perfil) in MOTORES_RAPIDOS:
            try:
                pcm = await asyncio.wait_for(
                    _voicebox_synthesize(text, config.voicebox_vivo_timeout, perfil), config.voicebox_vivo_timeout
                )
            except (asyncio.TimeoutError, httpx.HTTPError, ValueError, wave.Error) as exc:
                log.warning("Voicebox no llegó a tiempo para %r (%s): habla Piper", text[:40], exc or "tiempo")
        if not pcm:
            _encolar(text, params)
    elif perfil:
        try:
            pcm = await _voicebox_synthesize(
                text, timeout or config.voicebox_call_timeout, perfil
            )
        except (httpx.HTTPError, ValueError, wave.Error) as exc:
            log.warning("Voicebox no pudo sintetizar %r: %s", text[:50], exc)

    if not pcm:
        # Piper es sincrónico: a un thread, que el event loop atiende otras llamadas
        pcm = await asyncio.to_thread(_piper_synthesize, text, params)
        if pcm and perfil:
            # Respaldo: no se cachea, ni en disco ni en memoria. Si se guardara, esa frase
            # quedaría con voz de Piper para siempre (y en memoria taparía al precalentado).
            return pcm

    if not pcm:
        log.error("Ningún motor pudo sintetizar %r", text[:50])
        return b""

    _memory_cache[clave_memoria] = pcm
    _write_cache(text, pcm, params)
    return pcm


# ---------------------------------------------------------------------------
# Generación en segundo plano
# ---------------------------------------------------------------------------
_MAX_EN_COLA = 50
_en_cola: dict[tuple[str, str], tuple[str, VoiceParams]] = {}
_tarea: asyncio.Task | None = None


def _encolar(text: str, params: VoiceParams) -> None:
    """Pide generar `text` con la voz de Voicebox sin esperar. Una frase por vez, sin repetidas."""
    global _tarea
    clave = (text, params.clave())
    if clave in _en_cola or len(_en_cola) >= _MAX_EN_COLA:
        return
    _en_cola[clave] = (text, params)
    if _tarea is None or _tarea.done():
        _tarea = asyncio.create_task(_vaciar_cola())


async def _vaciar_cola() -> None:
    while _en_cola:
        clave, (text, params) = next(iter(_en_cola.items()))
        try:
            await warm_up([text], params)
        except Exception:  # noqa: BLE001 - el segundo plano nunca baja el servicio
            log.exception("Falló la generación en segundo plano de %r", text[:50])
        finally:
            _en_cola.pop(clave, None)


def precalentar_en_segundo_plano(texts: list[str], params: VoiceParams = DEFAULT_PARAMS) -> None:
    """Encola lo que falte del guion de una campaña, sin bloquear la llamada que lo pidió."""
    if not (perfil_de_voicebox(params) or perfil_de_voicestudio(params) or perfil_de_elevenlabs(params)):
        return
    for t in dict.fromkeys(f for texto in texts for f in partir_frases(texto)):
        if (t, params.clave()) not in _memory_cache and _read_cache(t, params) is None:
            _encolar(t, params)


async def warm_up(texts: list[str], params: VoiceParams = DEFAULT_PARAMS) -> int:
    """Pre-sintetiza frases fijas. Devuelve cuántas se generaron nuevas.

    Se corre al arrancar el servicio, no durante una llamada: con voicebox cada
    frase puede tardar decenas de segundos y del otro lado habría un cliente
    escuchando silencio.
    """
    pending = [
        t for t in dict.fromkeys(f for texto in texts for f in partir_frases(texto))
        # Un "{nombre}" sin reemplazar es una plantilla, no algo que se diga: no se sintetiza.
        if "{nombre}" not in t and (t, params.clave()) not in _memory_cache
    ]
    if not pending:
        return 0

    generated = 0
    for text in pending:
        if _read_cache(text, params) is not None:
            continue

        await _sintetizar_frase(text, config.voicebox_warmup_timeout, params)

        # Solo cuenta si quedó en disco. Si respondió Piper de respaldo no se
        # cachea a propósito, y decir "precalentado" ahí sería mentirle al log.
        if _read_cache(text, params) is not None:
            generated += 1
            log.info("Precalentado (%d/%d): %r", generated, len(pending), text[:60])
        else:
            log.warning("No quedó cacheado: %r", text[:60])

    return generated
