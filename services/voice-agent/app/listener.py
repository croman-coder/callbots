"""Detección de fin de respuesta con webrtcvad.

El problema: no sabemos cuánto va a hablar el cliente. Cortar por tiempo fijo
trunca las respuestas largas y hace esperar de más las cortas. Con VAD se corta
cuando el cliente efectivamente dejó de hablar.

Máquina de estados:

    ESPERANDO_VOZ ──(detecta voz)──► HABLANDO ──(silencio sostenido)──► LISTO
          │
          └──(nadie habla en N segundos)──► SILENCIO
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import webrtcvad

from app.audiosocket import AudioSocket, AudioSocketClosed
from app.config import BYTES_PER_FRAME, FRAME_MS, SAMPLE_RATE, config

log = logging.getLogger(__name__)


class ListenResult(str, Enum):
    SPEECH = "speech"        # habló y terminó
    SILENCE = "silence"      # nunca habló
    TIMEOUT = "timeout"      # habló y se cortó por límite de tiempo
    HANGUP = "hangup"        # cortó la llamada


@dataclass
class Recording:
    pcm: bytes
    result: ListenResult
    speech_ms: int

    @property
    def has_audio(self) -> bool:
        return self.result in (ListenResult.SPEECH, ListenResult.TIMEOUT)

    @property
    def duration_seconds(self) -> float:
        return len(self.pcm) / (SAMPLE_RATE * 2)


async def listen(
    socket: AudioSocket,
    max_seconds: int | None = None,
    silence_ms: int | None = None,
    no_speech_timeout: int | None = None,
) -> Recording:
    """Escucha hasta que el cliente deja de hablar.

    Solo se guarda el audio desde que arranca la voz (más un colchón previo),
    para no mandarle a Whisper varios segundos de silencio inicial.
    """
    max_seconds = max_seconds or config.max_answer_seconds
    silence_ms = silence_ms or config.silence_ms_to_stop
    no_speech_timeout = no_speech_timeout or config.no_speech_timeout_seconds

    vad = webrtcvad.Vad(config.vad_aggressiveness)

    max_frames = max_seconds * 1000 // FRAME_MS
    silence_frames_to_stop = silence_ms // FRAME_MS
    no_speech_frames = no_speech_timeout * 1000 // FRAME_MS
    # Colchón: 200 ms antes del inicio detectado, para no comer la primera sílaba
    prebuffer_frames = 10

    collected: list[bytes] = []
    prebuffer: list[bytes] = []

    speaking = False
    speech_frames = 0
    silence_run = 0
    total_frames = 0

    while True:
        try:
            frame = await socket.read_audio_frame()
        except AudioSocketClosed:
            return Recording(
                pcm=b"".join(collected),
                result=ListenResult.HANGUP,
                speech_ms=speech_frames * FRAME_MS,
            )

        total_frames += 1

        # webrtcvad exige tramas exactas de 10/20/30 ms
        if len(frame) != BYTES_PER_FRAME:
            continue

        try:
            is_speech = vad.is_speech(frame, SAMPLE_RATE)
        except Exception:  # noqa: BLE001 - trama malformada: la tratamos como silencio
            is_speech = False

        if not speaking:
            prebuffer.append(frame)
            if len(prebuffer) > prebuffer_frames:
                prebuffer.pop(0)

            if is_speech:
                speaking = True
                collected.extend(prebuffer)
                collected.append(frame)
                speech_frames += 1
                silence_run = 0
            elif total_frames >= no_speech_frames:
                log.debug("Nadie habló en %ds", no_speech_timeout)
                return Recording(pcm=b"", result=ListenResult.SILENCE, speech_ms=0)
            continue

        # --- ya está hablando ---
        collected.append(frame)

        if is_speech:
            speech_frames += 1
            silence_run = 0
        else:
            silence_run += 1
            if silence_run >= silence_frames_to_stop:
                # Recortamos la cola de silencio, no aporta a la transcripción
                keep = max(0, len(collected) - silence_run + 5)
                return Recording(
                    pcm=b"".join(collected[:keep]),
                    result=ListenResult.SPEECH,
                    speech_ms=speech_frames * FRAME_MS,
                )

        if len(collected) >= max_frames:
            log.debug("Corte por límite de %ds", max_seconds)
            return Recording(
                pcm=b"".join(collected),
                result=ListenResult.TIMEOUT,
                speech_ms=speech_frames * FRAME_MS,
            )
