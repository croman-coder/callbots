"""Protocolo AudioSocket de Asterisk.

Formato de trama, sobre TCP:

    ┌────────┬──────────────┬─────────────┐
    │ 1 byte │   2 bytes    │   N bytes   │
    │  tipo  │ largo (BE)   │   payload   │
    └────────┴──────────────┴─────────────┘

Tipos:
    0x00  terminar (colgar). Sin payload.
    0x01  UUID de la sesión. Payload = 16 bytes. Asterisk lo manda primero.
    0x10  audio. Payload = PCM slin 8 kHz, 16-bit LE, mono (320 bytes = 20 ms).
    0xff  error. Payload = 1 byte con el código.

El reloj es el tiempo real, no la cantidad de tramas que llegan. La reproducción sale de a una trama cada 20 ms
medidos con el reloj del sistema, y quien escucha mide sus esperas en segundos. Contar tramas parecía más simple
(«por cada trama que entra, sale una»), pero el audio de entrada llega a ráfagas —el WebSocket del navegador junta
varias, y la red también— y cuando una no llegaba a tiempo había que inventar un silencio para no colgarse: cada
ráfaga se contaba dos veces. Medido el 21/10/2026: el agente creía haber dicho el saludo y la primera pregunta a los
18 s cuando duraban 31 s, escuchaba mientras la persona todavía oía el saludo y le metía silencios falsos a su voz.

La lectura del socket vive en una tarea aparte que llena una cola. Si el otro extremo no manda audio —NAT sin
abrir, troncal de una vía— el bot habla igual (su audio abre el NAT) y las esperas vencen a su hora.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as uuid_mod

from app.config import BYTES_PER_FRAME, FRAME_MS

log = logging.getLogger(__name__)

TYPE_TERMINATE = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
# Texto hacia quien prueba el agente desde el navegador (la transcripción en vivo). Solo viaja en esas
# sesiones: una llamada real de Asterisk nunca lo recibe.
TYPE_EVENT = 0x20
TYPE_ERROR = 0xFF

HEADER_SIZE = 3
SILENCE_FRAME = b"\x00" * BYTES_PER_FRAME
FRAME_SECONDS = FRAME_MS / 1000.0


class AudioSocketClosed(Exception):
    """El otro extremo cerró la conexión o mandó una trama de terminación."""


class AudioSocket:
    """Una conexión AudioSocket: un canal de Asterisk, una llamada."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._reader = reader
        self._writer = writer
        self.session_uuid: uuid_mod.UUID | None = None
        self.peer = writer.get_extra_info("peername")
        self.dtmf_digits: list[str] = []
        self.eventos = False
        # Lo que va leyendo la tarea de fondo, a la espera de que alguien lo
        # consuma. Sin tope: a 20 ms por trama, una llamada larga entra de
        # sobra en memoria, y descartar audio del cliente en silencio sería
        # perder justo lo que vino a escuchar.
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed: AudioSocketClosed | None = None
        self._pump: asyncio.Task[None] | None = None
        # Cuándo toca escribir la próxima trama de audio (reloj del loop, en segundos).
        self._reloj: float | None = None

    # ------------------------------------------------------------------ lectura
    async def read_frame(self) -> tuple[int, bytes]:
        """Lee una trama. Levanta AudioSocketClosed cuando termina la llamada."""
        try:
            header = await self._reader.readexactly(HEADER_SIZE)
        except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
            raise AudioSocketClosed("conexión cerrada por Asterisk") from exc

        frame_type = header[0]
        length = int.from_bytes(header[1:3], "big")

        payload = b""
        if length:
            try:
                payload = await self._reader.readexactly(length)
            except (asyncio.IncompleteReadError, ConnectionResetError) as exc:
                raise AudioSocketClosed("conexión cortada a mitad de trama") from exc

        if frame_type == TYPE_TERMINATE:
            raise AudioSocketClosed("Asterisk pidió terminar")

        if frame_type == TYPE_ERROR:
            code = payload[0] if payload else 0
            log.warning("AudioSocket reportó error 0x%02x", code)

        return frame_type, payload

    async def read_uuid(self, timeout: float = 5.0) -> uuid_mod.UUID:
        """Lee el UUID de sesión, que Asterisk manda como primera trama."""
        deadline = asyncio.get_running_loop().time() + timeout

        while asyncio.get_running_loop().time() < deadline:
            frame_type, payload = await self.read_frame()

            if frame_type == TYPE_UUID and len(payload) == 16:
                self.session_uuid = uuid_mod.UUID(bytes=payload)
                return self.session_uuid

            # Algunas versiones mandan audio antes del UUID: se descarta
            if frame_type == TYPE_AUDIO:
                continue

        raise AudioSocketClosed("no llegó el UUID de sesión")

    def start_pump(self) -> None:
        """Arranca la lectura de fondo. Se llama una vez leído el UUID."""
        if self._pump is None:
            self._pump = asyncio.create_task(self._pump_loop())

    async def _pump_loop(self) -> None:
        """Vacía el socket hacia la cola, sin que nadie tenga que estar leyendo.

        Que la lectura viva acá es lo que permite entregar silencio por timeout
        más abajo: cancelar una espera sobre la cola es inofensivo, mientras que
        cancelar un `readexactly` a mitad de trama desincronizaría el stream.
        """
        try:
            while True:
                frame_type, payload = await self.read_frame()

                if frame_type == TYPE_AUDIO:
                    await self._incoming.put(payload)

                elif frame_type == TYPE_DTMF and payload:
                    digit = payload.decode("ascii", errors="ignore")
                    self.dtmf_digits.append(digit)
                    log.debug("DTMF recibido: %s", digit)

        except AudioSocketClosed as exc:
            self._closed = exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - que no muera en silencio
            log.warning("La lectura del AudioSocket se cortó: %s", exc)
            self._closed = AudioSocketClosed(str(exc))

    async def siguiente_trama(self, espera: float) -> bytes | None:
        """La próxima trama de audio que mandó el otro extremo, o None si no llegó ninguna en `espera` segundos.

        Nunca inventa audio: lo que devuelve es lo que llegó, en orden. Quien escucha mide el tiempo con el reloj, no
        contando lo que le devuelve esta función.
        """
        if self._pump is None:
            # Nadie arrancó la tarea de fondo: se lee derecho.
            while True:
                frame_type, payload = await self.read_frame()
                if frame_type == TYPE_AUDIO:
                    return payload
        if not self._incoming.empty():
            return self._incoming.get_nowait()
        if self._closed is not None:
            raise self._closed
        try:
            return await asyncio.wait_for(self._incoming.get(), timeout=espera)
        except asyncio.TimeoutError:
            if self._closed is not None and self._incoming.empty():
                raise self._closed
            return None

    def _descartar_entrante(self) -> None:
        """Tira lo que el otro extremo mandó mientras habla el bot: si no, su voz se mezclaría con la respuesta."""
        while not self._incoming.empty():
            self._incoming.get_nowait()

    async def _a_tiempo(self) -> None:
        """Espera hasta que toque la próxima trama: una cada 20 ms de reloj real. Si se atrasó (el proceso estuvo
        ocupado), no se pone al día de golpe más de 100 ms: una ráfaga larga se oiría como un tropezón."""
        ahora = asyncio.get_running_loop().time()
        if self._reloj is None or self._reloj < ahora - 0.1:
            self._reloj = ahora
        self._reloj += FRAME_SECONDS
        espera = self._reloj - ahora
        if espera > 0:
            await asyncio.sleep(espera)

    # ----------------------------------------------------------------- escritura
    def _write_frame(self, frame_type: int, payload: bytes = b"") -> None:
        self._writer.write(
            bytes([frame_type]) + len(payload).to_bytes(2, "big") + payload
        )

    async def play(self, pcm: bytes, drain_input: bool = True) -> None:
        """Reproduce PCM 8 kHz en el canal, una trama cada 20 ms de reloj real.

        El audio que llega durante la reproducción se descarta: si no, la voz del bot se mezclaría con la respuesta
        del cliente en la transcripción.
        """
        for offset in range(0, len(pcm), BYTES_PER_FRAME):
            chunk = pcm[offset : offset + BYTES_PER_FRAME]
            if len(chunk) < BYTES_PER_FRAME:
                chunk = chunk.ljust(BYTES_PER_FRAME, b"\x00")
            await self._escribir_audio(chunk, drain_input)

    async def _escribir_audio(self, chunk: bytes, descartar: bool = True) -> None:
        if self._closed is not None:
            raise self._closed
        if descartar:
            self._descartar_entrante()
        try:
            self._write_frame(TYPE_AUDIO, chunk)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError) as exc:
            # El otro extremo cortó mientras hablaba el bot: es un corte, no un error del servicio.
            raise AudioSocketClosed(f"se cortó mientras hablaba el bot ({exc.__class__.__name__})") from exc
        await self._a_tiempo()

    async def send_event(self, rol: str, texto: str) -> None:
        """Una línea de la transcripción para el navegador (rol: «agente» o «cliente»)."""
        if not self.eventos or not texto:
            return
        payload = json.dumps({"rol": rol, "texto": texto}, ensure_ascii=False).encode()[:60000]
        try:
            self._write_frame(TYPE_EVENT, payload)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    async def play_silence(self, milliseconds: int) -> None:
        """Silencio activo: mantiene el canal abierto y el reloj andando."""
        for _ in range(max(0, milliseconds // 20)):
            await self._escribir_audio(SILENCE_FRAME)

    async def hangup(self) -> None:
        try:
            self._write_frame(TYPE_TERMINATE)
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, AudioSocketClosed):
                pass
            self._pump = None

        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
