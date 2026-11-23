"""El Whisper de Callbot (app/stt.py, el mismo de producción) como STT de LiveKit Agents.

LiveKit le pasa al STT cada turno ya recortado por su VAD; acá se hace lo mismo que `_transcribir_con_pista` de app/stt.py: nivel
parejo, aire a los lados, sin el filtro de voz de Whisper y las respuestas posibles como pista (los números de la encuesta, o el
sí/no del saludo), con la vuelta al camino normal si Whisper «cuenta» o repite la pista. A diferencia de la telefonía, el audio llega
a 16 kHz o más (WebRTC/Opus), sin pasar por 8 kHz.
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np
import soxr
from livekit import rtc
from livekit.agents import stt
from livekit.agents.language import LanguageCode

from app import stt as nucleo

from .texto import MARCA_INAUDIBLE, hay_bucle

log = logging.getLogger("callbot.stt")
TASA = 16000
AIRE_SEGUNDOS = 0.3
INAUDIBLE_DESDE_SEGUNDOS = 1.0  # el tramo de VAD lleva ~0,5 s de relleno: menos que esto es un ruido, no una frase


def _unir(partes: list[tuple[str, float | None]]) -> str:
    return " ".join(t for t, _ in partes if t).strip()


def transcribir(audio: np.ndarray, pista: str | None) -> str:
    """`audio`: float32 mono a 16 kHz. Devuelve el texto (vacío si Whisper inventó una frase donde había ruido)."""
    aire = np.zeros(int(TASA * AIRE_SEGUNDOS), dtype=np.float32)
    x = np.concatenate([aire, nucleo._normalizar(audio), aire])
    if pista:
        texto = _unir(nucleo._decodificar(x, vad_filter=False, hotwords=pista))
        malo = nucleo.es_conteo(texto) if pista == nucleo.NUMEROS else nucleo.es_eco_del_prompt(texto, pista)
        if texto and not malo and not hay_bucle(texto) and not nucleo.es_alucinacion(texto):
            return texto
        log.info("con la pista salió %r: se transcribe sin ella", texto[:60])
    texto = _unir(nucleo._decodificar(x, vad_filter=False))
    if hay_bucle(texto):
        log.info("Whisper se quedó repitiendo (%r): se descarta", texto[:60])
        return ""
    return "" if nucleo.es_alucinacion(texto) else texto


class WhisperCallbot(stt.STT):
    """`estado["pista"]` lo cambia el agente según la pregunta que está haciendo."""

    def __init__(self, estado: dict) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._estado = estado

    @property
    def model(self) -> str:
        return f"whisper:{nucleo.config.whisper_model}"

    @property
    def provider(self) -> str:
        return "callbot"

    async def _recognize_impl(self, buffer, *, language=None, conn_options=None) -> stt.SpeechEvent:  # noqa: ANN001
        frame = rtc.combine_audio_frames(buffer)
        x = np.frombuffer(frame.data, dtype=np.int16).astype(np.float32) / 32768.0
        if frame.num_channels > 1:
            x = x.reshape(-1, frame.num_channels).mean(axis=1)
        if frame.sample_rate != TASA:
            x = soxr.resample(x, frame.sample_rate, TASA)
        inicio = time.monotonic()
        texto = await asyncio.to_thread(transcribir, x, self._estado.get("pista"))
        log.info("STT %.2f s de audio -> %r (%.2f s)", len(x) / TASA, texto, time.monotonic() - inicio)
        if not texto and len(x) / TASA >= INAUDIBLE_DESDE_SEGUNDOS:
            # Alguien habló y Whisper no sacó nada útil (se quedó repitiendo o inventó una frase): un texto vacío no abre turno y la persona
            # se queda hablándole al aire. Con este marcador el guion repregunta («perdón, ¿me lo repite?»), igual que ante un número que no se entendió.
            texto = MARCA_INAUDIBLE
        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[stt.SpeechData(language=LanguageCode("es"), text=texto)],
        )
