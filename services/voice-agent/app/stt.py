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
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass

import numpy as np
import soxr

from app.config import SAMPLE_RATE, WHISPER_SAMPLE_RATE, config

log = logging.getLogger(__name__)

_model = None
# Si la GPU falla en plena llamada se atiende en CPU (ver _decodificar). Se carga recién cuando hace falta.
_cpu_model = None
_cpu_lock = threading.Lock()
_gpu_mala_hasta = 0.0


@dataclass
class Transcription:
    text: str
    confidence: float | None
    duration: float


def load_model() -> None:
    """Carga el modelo una vez. En GPU tarda unos segundos."""
    global _model, _cpu_model

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
            _cpu_model = _model
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
    """Whisper devolvió su propia guía en vez de lo que se dijo.

    Pasa con audios cortos: el cliente dijo «Ocho.» y salió «Encuesta telefónica de satisfacción de un taller
    mecánico.» (prueba e2e del 22/10/2026). Es eco si la mayor parte del texto es UN TRAMO SEGUIDO de la guía, en su
    mismo orden. No alcanza con que las palabras estén en la guía: un teléfono dicho en dígitos («cero nueve ocho uno
    cuatro…») usa solo palabras de la lista de números y no es eco (24/10/2026: se lo tomó por eco, se volvió a
    transcribir sin guía y salió peor). Una respuesta de verdad («ocho», «cero, nunca») tiene pocas palabras o no copia
    un tramo largo de la guía.
    """
    dichas = _palabras(texto)
    if len(dichas) < 4:
        return False
    de_la_guia = _palabras(prompt)
    tramo = SequenceMatcher(None, dichas, de_la_guia, autojunk=False).find_longest_match(0, len(dichas), 0, len(de_la_guia))
    return tramo.size >= 4 and tramo.size / len(dichas) >= 0.7


# Las palabras posibles cuando la pregunta se contesta con un número. Va como `hotwords` y no como el texto de la guía
# (ver prompt_para): sesga a escribir «Diez.» en vez de «Dies», «Vies», «NERF» o nada. Medido el 2/11/2026 con los 42
# audios guardados: arregló «Dies»→«Diez.», «NERF»→«nueve» y los vacíos, pero con audio dudoso a veces «cuenta»
# («uno, dos, tres…»): eso se detecta (es_conteo) y se vuelve al camino de siempre.
NUMEROS = "cero, uno, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."
_NUMEROS = {"cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"}
# Aire que se le deja a una respuesta numérica a cada lado (siempre: «diez» dura 0,5 s y el final suena a «s»).
MARGEN_NUMERO_SEGUNDOS = 0.3
# Con cuántos números distintos se da por hecho que Whisper contó en vez de transcribir (igual que scoring.py).
CONTEO_MIN = 4
# Cuánto se espera para volver a probar la GPU después de que falló.
ESPERA_GPU_MALA_SEGUNDOS = 60.0


def es_conteo(texto: str) -> bool:
    """Whisper devolvió la lista de números («uno, dos, tres, cuatro…») en vez de lo que se dijo."""
    palabras = _palabras(texto)
    hallados = {p for p in palabras if p in _NUMEROS} | {p for p in palabras if p.isdigit() and int(p) <= 10}
    return len(hallados) >= CONTEO_MIN


def _normalizar(audio: np.ndarray) -> np.ndarray:
    """Sube el audio hasta un pico de 0,5 (como mucho 8 veces): una respuesta suave no queda por debajo de lo que
    Whisper separa del ruido."""
    pico = float(np.max(np.abs(audio))) if audio.size else 0.0
    if pico <= 0.0:
        return audio
    return audio * min(0.5 / pico, 8.0)


def _transcribe_sync(pcm: bytes, vocabulario: list[str] | None = None, numeros: bool = False) -> Transcription:
    if numeros:
        resultado = _transcribir_numero(pcm)
        if resultado.text and not es_conteo(resultado.text) and not es_alucinacion(resultado.text):
            return resultado
        log.info("Respuesta numérica: %r no sirve, se transcribe como siempre", resultado.text[:60])
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


def _modelo_cpu():
    """El Whisper de respaldo, en CPU. Se carga la primera vez que la GPU falla (unos segundos y ~2 GB de memoria)."""
    global _cpu_model
    with _cpu_lock:
        if _cpu_model is None:
            from faster_whisper import WhisperModel

            log.warning("Cargando Whisper %s en CPU como respaldo de la GPU...", config.whisper_model)
            _cpu_model = WhisperModel(config.whisper_model, device="cpu", compute_type="int8", download_root="/models/whisper")
        return _cpu_model


def _es_error_de_gpu(exc: BaseException) -> bool:
    texto = str(exc).lower()
    return any(m in texto for m in ("cuda", "cudnn", "cublas", "out of memory", "device ordinal", "gpu"))


def _correr(modelo, audio: np.ndarray, kwargs: dict) -> list[tuple[str, float | None]]:
    """Corre Whisper y recorre los segmentos: el decodificador trabaja recién ahí, así que los errores salen acá."""
    segments, _info = modelo.transcribe(
        audio,
        language=config.whisper_language,
        beam_size=5,
        # Sin contexto previo: cada respuesta es independiente y arrastrar el
        # texto anterior hace que Whisper invente continuaciones.
        condition_on_previous_text=False,
        **kwargs,
    )
    return [(s.text.strip(), s.avg_logprob) for s in segments]


def _decodificar(audio: np.ndarray, **kwargs) -> list[tuple[str, float | None]]:
    """Whisper en la GPU y, si la GPU falla, en CPU: antes el error tumbaba la llamada entera.

    El 2/11/2026 dos llamadas de prueba murieron con «CUDA out of memory» y «invalid device ordinal»: la GPU de 12 GB
    la comparten Whisper (2,2 GB), OmniVoice (4,4 GB) y el Ollama de Hermes (4,5 GB). Una falla se reintenta una vez (a
    veces es un pico de otro proceso) y, si sigue, esta respuesta y las de los próximos segundos van por CPU.
    """
    global _gpu_mala_hasta
    en_cpu = config.whisper_device == "cpu" or _model is _cpu_model
    if not en_cpu and time.monotonic() >= _gpu_mala_hasta:
        for intento in (1, 2):
            try:
                return _correr(_model, audio, kwargs)
            except RuntimeError as exc:
                if not _es_error_de_gpu(exc):
                    raise
                log.error("La GPU falló transcribiendo (intento %d de 2): %s", intento, exc)
                if intento == 1:
                    time.sleep(0.3)
        _gpu_mala_hasta = time.monotonic() + ESPERA_GPU_MALA_SEGUNDOS
        log.error("Se transcribe en CPU durante %d s y se vuelve a probar la GPU", ESPERA_GPU_MALA_SEGUNDOS)
    return _correr(_model if en_cpu else _modelo_cpu(), audio, kwargs)


def _transcribir_numero(pcm: bytes) -> Transcription:
    """Respuesta a una pregunta que se contesta con un número: sin el filtro de voz de Whisper (la respuesta ya llega
    recortada por la escucha, y el filtro se comía la «z» final), con aire a los lados, el nivel parejo y las palabras
    posibles como `hotwords`."""
    duration = len(pcm) / (SAMPLE_RATE * 2)
    aire = np.zeros(int(WHISPER_SAMPLE_RATE * MARGEN_NUMERO_SEGUNDOS), dtype=np.float32)
    audio = np.concatenate([aire, _normalizar(_pcm_to_float32(pcm)), aire])
    partes = _decodificar(audio, vad_filter=False, hotwords=NUMEROS)
    return _armar(partes, duration)


def _transcribir(pcm: bytes, prompt: str | None, margen: bool = False) -> Transcription:
    duration = len(pcm) / (SAMPLE_RATE * 2)
    audio = _pcm_to_float32(pcm)
    if margen:
        aire = np.zeros(int(WHISPER_SAMPLE_RATE * MARGEN_SEGUNDOS), dtype=np.float32)
        audio = np.concatenate([aire, audio, aire])

    partes = _decodificar(
        audio,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        # Sesgo hacia el dominio: mejora el reconocimiento de los números
        # hablados. Tiene que nombrar el rango real (0-10): si dice "uno al
        # cinco", Whisper transcribe "nueve" y "diez" mucho peor, y son
        # justamente los valores que definen si el cliente quedó conforme.
        initial_prompt=prompt,
    )
    return _armar(partes, duration)


def _armar(partes: list[tuple[str, float | None]], duration: float) -> Transcription:
    parts = [texto for texto, _ in partes]
    logprobs = [lp for _, lp in partes if lp is not None]
    text = " ".join(p for p in parts if p).strip()

    # avg_logprob es log-probabilidad; exp() lo lleva a un 0-1 interpretable
    confidence = None
    if logprobs:
        confidence = round(min(1.0, math.exp(sum(logprobs) / len(logprobs))), 3)

    log.debug("Transcripción (%.1fs, conf=%s): %r", duration, confidence, text)
    return Transcription(text=text, confidence=confidence, duration=duration)


async def transcribe(pcm: bytes, vocabulario: list[str] | None = None, numeros: bool = False) -> Transcription:
    """Transcribe en un thread aparte: el modelo bloquea el event loop. `numeros`: la pregunta se contesta con un número."""
    if len(pcm) < MIN_SEGUNDOS * SAMPLE_RATE * 2:  # un golpe, no una palabra: no hay nada que transcribir
        return Transcription(text="", confidence=None, duration=len(pcm) / (SAMPLE_RATE * 2))

    return await asyncio.to_thread(_transcribe_sync, pcm, vocabulario, numeros)
