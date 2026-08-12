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
"""

from __future__ import annotations

import asyncio
import logging
import uuid as uuid_mod

from app.config import BYTES_PER_FRAME

log = logging.getLogger(__name__)

TYPE_TERMINATE = 0x00
TYPE_UUID = 0x01
TYPE_DTMF = 0x03
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xFF

HEADER_SIZE = 3
SILENCE_FRAME = b"\x00" * BYTES_PER_FRAME


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

    async def read_audio_frame(self) -> bytes:
        """Devuelve el próximo bloque de audio entrante, salteando lo demás."""
        while True:
            frame_type, payload = await self.read_frame()

            if frame_type == TYPE_AUDIO:
                return payload

            if frame_type == TYPE_DTMF and payload:
                digit = payload.decode("ascii", errors="ignore")
                self.dtmf_digits.append(digit)
                log.debug("DTMF recibido: %s", digit)

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
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass
