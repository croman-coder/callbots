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

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

import webrtcvad

from app.audiosocket import AudioSocket, AudioSocketClosed
from app.config import BYTES_PER_FRAME, FRAME_MS, SAMPLE_RATE, config

log = logging.getLogger(__name__)


def pico_de(frame: bytes) -> int:
    """Amplitud máxima de la trama, para saber si hay señal de verdad."""
    return max(
        (
            abs(int.from_bytes(frame[i : i + 2], "little", signed=True))
            for i in range(0, len(frame), 2)
        ),
        default=0,
    )


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


# Cada cuánto se revisan las esperas cuando no llega audio. Corto, para cortar a tiempo; no inventa tramas.
ESPERA_TRAMA = 0.1


async def listen(
    socket: AudioSocket,
    max_seconds: int | None = None,
    silence_ms: int | None = None,
    no_speech_timeout: int | None = None,
) -> Recording:
    """Escucha hasta que el cliente deja de hablar.

    Solo se guarda el audio desde que arranca la voz (más un colchón previo), para no mandarle a Whisper varios
    segundos de silencio inicial. Las esperas (nadie habla, silencio al final, tope) se miden con el reloj, no
    contando tramas: el audio llega a ráfagas y contar tramas acortaba las esperas a la mitad (ver audiosocket.py).
    Lo grabado es solo lo que llegó, en orden: nunca se le intercalan silencios inventados.
    """
    max_seconds = max_seconds or config.max_answer_seconds
    silence_ms = silence_ms or config.silence_ms_to_stop
    no_speech_timeout = no_speech_timeout or config.no_speech_timeout_seconds

    vad = webrtcvad.Vad(config.vad_aggressiveness)
    reloj = asyncio.get_running_loop().time

    max_frames = max_seconds * 1000 // FRAME_MS
    silencio_para_cortar = silence_ms / 1000
    # Colchón: 200 ms antes del inicio detectado, para no comer la primera sílaba
    prebuffer_frames = 10

    collected: list[bytes] = []
    prebuffer: list[bytes] = []

    speaking = False
    speech_frames = 0
    tramas_desde_voz = 0
    inicio = reloj()
    ultima_voz = inicio

    while True:
        try:
            frame = await socket.siguiente_trama(ESPERA_TRAMA)
        except AudioSocketClosed:
            return Recording(
                pcm=b"".join(collected),
                result=ListenResult.HANGUP,
                speech_ms=speech_frames * FRAME_MS,
            )
        ahora = reloj()

        # webrtcvad exige tramas exactas de 10/20/30 ms
        if frame is not None and len(frame) == BYTES_PER_FRAME:
            try:
                is_speech = vad.is_speech(frame, SAMPLE_RATE)
            except Exception:  # noqa: BLE001 - trama malformada: la tratamos como silencio
                is_speech = False

            # webrtcvad se entusiasma: el ruido de la sala, el aire de la línea o el eco del propio bot por los
            # parlantes le alcanzan para decir "voz". Con eso el silencio final nunca se acumula y la escucha se
            # estira hasta el tope. El piso de energía descarta lo que suena pero no es alguien hablando.
            if is_speech and pico_de(frame) < config.speech_floor:
                is_speech = False

            if not speaking:
                if is_speech:
                    speaking = True
                    collected.extend(prebuffer)
                    collected.append(frame)
                    prebuffer.clear()
                    speech_frames = 1
                    tramas_desde_voz = 0
                    ultima_voz = ahora
                else:
                    prebuffer.append(frame)
                    if len(prebuffer) > prebuffer_frames:
                        prebuffer.pop(0)
            else:
                collected.append(frame)
                if is_speech:
                    speech_frames += 1
                    tramas_desde_voz = 0
                    ultima_voz = ahora
                else:
                    tramas_desde_voz += 1

        if not speaking:
            if ahora - inicio >= no_speech_timeout:
                log.debug("Nadie habló en %ds", no_speech_timeout)
                return Recording(pcm=b"", result=ListenResult.SILENCE, speech_ms=0)
            continue

        # --- ya está hablando ---
        if ahora - ultima_voz >= silencio_para_cortar:
            # Recortamos la cola de silencio (dejando 100 ms), no aporta a la transcripción
            keep = max(0, len(collected) - tramas_desde_voz + 5)
            return Recording(
                pcm=b"".join(collected[:keep]),
                result=ListenResult.SPEECH,
                speech_ms=speech_frames * FRAME_MS,
            )

        if len(collected) >= max_frames or ahora - inicio >= max_seconds + no_speech_timeout:
            log.debug("Corte por límite de %ds", max_seconds)
            return Recording(
                pcm=b"".join(collected),
                result=ListenResult.TIMEOUT,
                speech_ms=speech_frames * FRAME_MS,
            )


def es_ruido(grabacion: Recording, transcripcion: str) -> bool:
    """Sonó algo corto (un golpe, una respiración, el «eh» antes de hablar) y Whisper no sacó ni una palabra.

    No es una respuesta: quien escucha sigue escuchando sin decir nada. En el Simulador pasó dos veces en una misma
    encuesta (21/10/2026): 0,3 s de ruido, «¿me lo repite?», y parecía que el agente no escuchaba.
    """
    return grabacion.has_audio and not (transcripcion or "").strip() and grabacion.duration_seconds < 1.2


async def voz_mientras(
    socket: AudioSocket, tarea: asyncio.Future, voz_ms: int = 200, ventana_ms: int = 300, gracia_ms: int = 400
) -> list[bytes] | None:
    """Mientras corre `tarea` (la transcripción del turno), sigue leyendo lo que manda el cliente.

    Si vuelve a hablar (`voz_ms` de voz dentro de una ventana de `ventana_ms`), devuelve lo que llegó desde un poco
    antes de que arrancara, para que quien llama siga escuchando y lo junte con lo anterior. Si no, None.

    Por qué: el cliente que cuenta un problema hace pausas largas entre oraciones («…nadie me avisó nada. Estoy
    molesto.»). Con el corte por silencio la segunda oración llegaba mientras el agente pensaba, y se tiraba cuando
    empezaba a hablar. Se mira mientras se transcribe, antes de mandarle nada a Claude: no deja un turno a medias en
    la conversación. Se pide voz dentro de una ventana y no seguida porque las consonantes («s», «b», «t») cortan la
    racha (medido con una voz real: 300 ms seguidos llegaban tarde, después de la transcripción). Si al terminar la
    transcripción ya empezó a sonar algo, se espera hasta `gracia_ms` para confirmarlo; si no suena nada, no se
    espera nada.
    """
    vad = webrtcvad.Vad(config.vad_aggressiveness)
    reloj = asyncio.get_running_loop().time
    leidas: list[bytes] = []
    marcas: list[bool] = []
    necesarias = max(1, voz_ms // FRAME_MS)
    ventana = max(necesarias, ventana_ms // FRAME_MS)
    hasta: float | None = None
    while True:
        if tarea.done():
            if not any(marcas[-10:]):  # nada sonando en los últimos 200 ms: terminó de hablar
                return None
            if hasta is None:
                hasta = reloj() + gracia_ms / 1000
            elif reloj() >= hasta:
                return None
        try:
            frame = await socket.siguiente_trama(0.05)
        except AudioSocketClosed:
            return None  # cortó: lo ve la próxima escucha
        if frame is None or len(frame) != BYTES_PER_FRAME:
            continue
        try:
            voz = vad.is_speech(frame, SAMPLE_RATE) and pico_de(frame) >= config.speech_floor
        except Exception:  # noqa: BLE001 - trama malformada: silencio
            voz = False
        leidas.append(frame)
        marcas.append(voz)
        if sum(marcas[-ventana:]) >= necesarias:
            # Desde 200 ms antes de la ventana, para no comer la primera sílaba.
            return leidas[-(ventana + 10):]
