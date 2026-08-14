"""Simulador de llamada desde el navegador.

Hace de puente entre el micrófono del navegador y el AudioSocket del
voice-agent, que es el mismo punto al que Asterisk entrega el audio de una
llamada real:

    navegador  --WebSocket (PCM 8 kHz)-->  API  --AudioSocket (TCP)-->  voice-agent

Sirve para hablar con el bot con voz humana sin troncal ni softphone. Es la
única prueba que valida el reconocimiento de verdad: `scripts/simular_llamada.py`
usa voz sintetizada por el mismo Piper que habla el bot, así que mide el
circuito pero no la precisión con gente real.

Lo que NO cubre: la señalización SIP y el transporte RTP. Para eso hace falta
un softphone o la troncal.

La sesión va en modo demo (UUID en cero, igual que la extensión 9000): no toca
Bitrix, no marca destinatarios y no guarda resultados.
"""

from __future__ import annotations

import asyncio
import array
import logging
import secrets
import struct
import time
import uuid as uuid_mod

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings

log = logging.getLogger(__name__)

router = APIRouter(tags=["simulador"])

DEMO_UUID = uuid_mod.UUID("00000000-0000-0000-0000-000000000000")

TYPE_TERMINATE = 0x00
TYPE_UUID = 0x01
TYPE_AUDIO = 0x10

FRAME_BYTES = 320  # 20 ms de slin 8 kHz, 16-bit mono

# El panel está detrás de HTTP Basic, pero el navegador no manda esas
# credenciales al abrir un WebSocket. La página, que sí está autenticada,
# pide un ticket de un solo uso y lo pasa en la query.
_TICKET_TTL_SECONDS = 60
_tickets: dict[str, float] = {}


def emitir_ticket() -> str:
    """Ticket de un solo uso para abrir el WebSocket. Lo llama el panel."""
    _purgar_tickets()
    ticket = secrets.token_urlsafe(32)
    _tickets[ticket] = time.monotonic() + _TICKET_TTL_SECONDS
    return ticket


def _purgar_tickets() -> None:
    ahora = time.monotonic()
    for t in [t for t, vence in _tickets.items() if vence < ahora]:
        _tickets.pop(t, None)


def _consumir_ticket(ticket: str | None) -> bool:
    if not ticket:
        return False
    _purgar_tickets()
    vence = _tickets.pop(ticket, None)
    return vence is not None and vence >= time.monotonic()


def _frame(tipo: int, payload: bytes = b"") -> bytes:
    return bytes([tipo]) + struct.pack(">H", len(payload)) + payload


async def _navegador_a_agente(ws: WebSocket, writer: asyncio.StreamWriter) -> None:
    """PCM del micrófono -> tramas de AudioSocket, de a 320 bytes.

    Loguea el nivel cada 5 s: si alguien reporta que el bot no lo escucha, lo
    primero que hay que saber es si el audio llega y con cuánta señal. Un pico
    cerca de cero es micrófono mudo o mal capturado; un nivel normal manda la
    investigación al VAD o al reconocimiento.
    """
    pendiente = b""
    ultimo_reporte = time.monotonic()
    pico = 0
    bytes_totales = 0

    while True:
        data = await ws.receive_bytes()
        bytes_totales += len(data)
        if data:
            # audioop se saca en Python 3.13; el pico de un int16 se calcula
            # con array, que es stdlib y no se va a ningún lado.
            muestras = array.array("h")
            muestras.frombytes(data[: len(data) // 2 * 2])
            if muestras:
                pico = max(pico, max(abs(m) for m in muestras))

        pendiente += data
        while len(pendiente) >= FRAME_BYTES:
            writer.write(_frame(TYPE_AUDIO, pendiente[:FRAME_BYTES]))
            pendiente = pendiente[FRAME_BYTES:]
        await writer.drain()

        ahora = time.monotonic()
        if ahora - ultimo_reporte >= 5.0:
            log.info(
                "Simulador: micrófono %.1f s de audio, pico %d/32767 (%s)",
                bytes_totales / 2 / 8000,
                pico,
                "sin señal" if pico < 500 else "ok",
            )
            ultimo_reporte, pico, bytes_totales = ahora, 0, 0


async def _agente_a_navegador(ws: WebSocket, reader: asyncio.StreamReader) -> None:
    """Tramas del voice-agent -> PCM crudo para reproducir en el navegador."""
    while True:
        cabecera = await reader.readexactly(3)
        tipo = cabecera[0]
        largo = int.from_bytes(cabecera[1:3], "big")
        payload = await reader.readexactly(largo) if largo else b""

        if tipo == TYPE_TERMINATE:
            await ws.send_json({"evento": "colgo"})
            return
        if tipo == TYPE_AUDIO and payload:
            await ws.send_bytes(payload)


@router.websocket("/simulador/ws")
async def simulador_ws(websocket: WebSocket, ticket: str | None = None) -> None:
    if not _consumir_ticket(ticket):
        # 1008 = policy violation. Se rechaza antes del accept.
        await websocket.close(code=1008)
        log.warning("Simulador: ticket inválido o vencido")
        return

    await websocket.accept()

    try:
        reader, writer = await asyncio.open_connection(
            settings.audiosocket_host, settings.audiosocket_port
        )
    except OSError as exc:
        log.error("Simulador: no se pudo conectar al voice-agent: %s", exc)
        await websocket.send_json({"evento": "error", "detalle": str(exc)})
        await websocket.close()
        return

    log.info("Simulador: conectado al voice-agent, arrancando sesión de demo")
    writer.write(_frame(TYPE_UUID, DEMO_UUID.bytes))
    await writer.drain()
    await websocket.send_json({"evento": "listo"})

    subida = asyncio.create_task(_navegador_a_agente(websocket, writer))
    bajada = asyncio.create_task(_agente_a_navegador(websocket, reader))

    try:
        # El primero que termina corta la llamada: si el navegador se va, no
        # tiene sentido seguir hablándole al voice-agent, y viceversa.
        await asyncio.wait({subida, bajada}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for tarea in (subida, bajada):
            tarea.cancel()
        writer.write(_frame(TYPE_TERMINATE))
        try:
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()
        try:
            await websocket.close()
        except (RuntimeError, WebSocketDisconnect):
            # El navegador ya se fue: cerrar de nuevo tira WebSocketDisconnect
            # y ensucia el log con un traceback que no significa nada.
            pass
        log.info("Simulador: sesión terminada")
