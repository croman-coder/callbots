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

El pacing de la reproducción se toma del reloj de Asterisk: por cada trama de
audio que entra, se escribe una de salida. Así no hace falta un timer propio y
no hay drift ni desbordes de buffer.

Ese reloj, sin embargo, solo late mientras entre RTP. Si el otro extremo no
manda audio —NAT sin abrir, troncal de una vía— no entra ninguna trama, todo lo
que espere una se cuelga para siempre y el bot se queda mudo. Y como tampoco
sale RTP, el agujero de NAT que dejaría entrar el retorno nunca se abre: el
silencio se sostiene solo.

Por eso la lectura del socket vive en una tarea aparte que llena una cola, y
`read_audio_frame` entrega silencio si no llegó nada en lo que dura una trama.
El reloj sigue latiendo aunque el canal esté mudo: el bot habla igual, su audio
abre el NAT, y el conteo de tramas del que dependen los timeouts no se detiene.
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_mod

from app.config import BYTES_PER_FRAME, FRAME_MS

log = logging.getLogger(__name__)

TYPE_TERMINATE = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
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
        # Lo que va leyendo la tarea de fondo, a la espera de que alguien lo
        # consuma. Sin tope: a 20 ms por trama, una llamada larga entra de
        # sobra en memoria, y descartar audio del cliente en silencio sería
        # perder justo lo que vino a escuchar.
        self._incoming: asyncio.Queue[bytes] = asyncio.Queue()
        self._closed: AudioSocketClosed | None = None
        self._pump: asyncio.Task[None] | None = None

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

    async def read_audio_frame(self) -> bytes:
        """Próximo bloque de audio entrante, o silencio si no llegó ninguno.

        Devolver silencio en vez de esperar es lo que mantiene vivo el reloj
        cuando el otro extremo no manda RTP: quien reproduce sigue escribiendo
        y quien escucha sigue contando tramas para sus timeouts.
        """
        if self._pump is None:
            # Nadie arrancó la tarea: se lee derecho, como antes.
            while True:
                frame_type, payload = await self.read_frame()
                if frame_type == TYPE_AUDIO:
                    return payload

        try:
            return await asyncio.wait_for(
                self._incoming.get(), timeout=FRAME_SECONDS
            )
        except asyncio.TimeoutError:
            # Con la llamada terminada no hay más audio que esperar: lo que
            # sigue es el corte, no otro silencio.
            if self._closed is not None and self._incoming.empty():
                raise self._closed
            return SILENCE_FRAME

    # ----------------------------------------------------------------- escritura
    def _write_frame(self, frame_type: int, payload: bytes = b"") -> None:
        self._writer.write(
            bytes([frame_type]) + len(payload).to_bytes(2, "big") + payload
        )

    async def play(self, pcm: bytes, drain_input: bool = True) -> None:
        """Reproduce PCM 8 kHz en el canal, sincronizado con el reloj de Asterisk.

        Por cada trama entrante se escribe una saliente. El audio que llega
        durante la reproducción se descarta: si no, la voz del bot se mezclaría
        con la respuesta del cliente en la transcripción.
        """
        for offset in range(0, len(pcm), BYTES_PER_FRAME):
            chunk = pcm[offset : offset + BYTES_PER_FRAME]
            if len(chunk) < BYTES_PER_FRAME:
                chunk = chunk.ljust(BYTES_PER_FRAME, b"\x00")

            if drain_input:
                await self.read_audio_frame()

            self._write_frame(TYPE_AUDIO, chunk)
            await self._writer.drain()

    async def play_silence(self, milliseconds: int) -> None:
        """Silencio activo: mantiene el canal y deja hablar al cliente."""
        for _ in range(max(0, milliseconds // 20)):
            await self.read_audio_frame()
            self._write_frame(TYPE_AUDIO, SILENCE_FRAME)
            await self._writer.drain()

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
