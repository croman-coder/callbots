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

import array
import asyncio
import logging
import math
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


def rms_de(frame: bytes) -> float:
    muestras = array.array("h", frame)
    return math.sqrt(sum(m * m for m in muestras) / max(1, len(muestras)))


class Niveles:
    """Qué es voz de quien habla y qué es el ambiente, por nivel: para no tomar el ruido de una oficina por voz.

    webrtcvad dice «voz» con cualquier cosa que suene a voz: alguien hablando lejos, la tele, el aire. En una prueba
    desde una oficina (22/10/2026) el cliente decía «cero» y la escucha seguía 15 segundos, hasta el tope, porque el
    murmullo de atrás nunca dejaba juntar el silencio para cortar. Ahora una trama cuenta como voz si además:

    - supera el piso de ruido del ambiente por MARGEN_RUIDO (el piso se sigue por abajo: baja enseguida, sube
      despacio, así las voces de atrás no lo inflan), y
    - una vez que habló, no queda por debajo de FRACCION_VOZ del nivel de su propia voz (quien habla está cerca del
      micrófono; lo de atrás llega mucho más bajo).
    """

    MARGEN_RUIDO = 3.0     # +9,5 dB sobre el ambiente
    FRACCION_VOZ = 0.25    # −12 dB bajo la voz de quien habla

    def __init__(self) -> None:
        self.ruido: float | None = None
        self.voz: float | None = None

    def es_voz(self, frame: bytes, vad_dice: bool) -> bool:
        r = rms_de(frame)
        voz = vad_dice and pico_de(frame) >= config.speech_floor
        if voz and self.ruido is not None and r < self.ruido * self.MARGEN_RUIDO:
            voz = False
        if voz and self.voz is not None and r < self.voz * self.FRACCION_VOZ:
            voz = False
        if voz:
            # La voz: sube rápido con lo fuerte, baja muy despacio.
            self.voz = r if self.voz is None or r > self.voz else self.voz * 0.998
        elif self.ruido is None or r < self.ruido:
            # El piso, solo con lo que no es voz: baja de golpe y sube despacio (~2 dB por segundo).
            self.ruido = max(r, 1.0)
        else:
            self.ruido *= 1.012
        return voz


class Interrupcion:
    """¿El cliente empezó a hablar mientras habla el agente? (barge-in)

    Sin esto el agente no escuchaba mientras hablaba: si el cliente contestaba antes de que terminara la pregunta,
    su respuesta se tiraba y quedaban los dos hablando a la vez («habla todo encimado», 22/10/2026). Ahora, si suena
    voz de verdad (VOZ_MS dentro de VENTANA_MS, el doble del piso de energía para no tomar el eco del propio agente
    por voz), el agente se calla y esa voz es el arranque de la respuesta.
    """

    VOZ_MS = 300
    VENTANA_MS = 400

    def __init__(self) -> None:
        self._vad = webrtcvad.Vad(config.vad_aggressiveness)
        self._niveles = Niveles()
        self._leidas: list[bytes] = []
        self._marcas: list[bool] = []
        self._necesarias = self.VOZ_MS // FRAME_MS
        self._ventana = self.VENTANA_MS // FRAME_MS

    def mirar(self, frame: bytes) -> bool:
        if len(frame) != BYTES_PER_FRAME:
            return False
        try:
            vad = self._vad.is_speech(frame, SAMPLE_RATE)
        except Exception:  # noqa: BLE001 - trama malformada: silencio
            vad = False
        voz = self._niveles.es_voz(frame, vad and pico_de(frame) >= 2 * config.speech_floor)
        self._leidas.append(frame)
        self._marcas.append(voz)
        del self._leidas[:-50], self._marcas[:-50]
        return sum(self._marcas[-self._ventana:]) >= self._necesarias

    def inicio(self) -> list[bytes]:
        """Lo que llegó desde un poco antes de que arrancara a hablar."""
        return self._leidas[-(self._ventana + 10):]


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
    previo: list[bytes] | None = None,
) -> Recording:
    """Escucha hasta que el cliente deja de hablar. `previo`: el arranque de su voz si interrumpió al agente
    (ver Interrupcion); la escucha sigue desde ahí, ya hablando.

    Solo se guarda el audio desde que arranca la voz (más un colchón previo), para no mandarle a Whisper varios
    segundos de silencio inicial. Las esperas (nadie habla, silencio al final, tope) se miden con el reloj, no
    contando tramas: el audio llega a ráfagas y contar tramas acortaba las esperas a la mitad (ver audiosocket.py).
    Lo grabado es solo lo que llegó, en orden: nunca se le intercalan silencios inventados.
    """
    max_seconds = max_seconds or config.max_answer_seconds
    silence_ms = silence_ms or config.silence_ms_to_stop
    no_speech_timeout = no_speech_timeout or config.no_speech_timeout_seconds

    vad = webrtcvad.Vad(config.vad_aggressiveness)
    niveles = Niveles()
    reloj = asyncio.get_running_loop().time

    max_frames = max_seconds * 1000 // FRAME_MS
    silencio_para_cortar = silence_ms / 1000
    # Colchón: 200 ms antes del inicio detectado, para no comer la primera sílaba
    prebuffer_frames = 10

    collected: list[bytes] = []
    prebuffer: list[bytes] = []

    speaking = bool(previo)
    if previo:
        collected.extend(previo)
    speech_frames = len(previo or [])
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

            # webrtcvad se entusiasma: el ruido de la sala, el aire de la línea, voces de atrás o el eco del propio
            # bot le alcanzan para decir "voz". Con eso el silencio final nunca se acumula y la escucha se estira
            # hasta el tope. El piso de energía y los niveles (ver Niveles) descartan lo que suena pero no es quien
            # está hablando.
            is_speech = niveles.es_voz(frame, is_speech)

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
