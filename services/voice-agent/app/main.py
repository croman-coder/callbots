"""Servidor AudioSocket.

Un TCP listener: Asterisk abre una conexión por llamada. Los modelos (Whisper y
Piper) se cargan una sola vez al arrancar y se comparten entre llamadas.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app import stt, tts
from app.api_client import ApiClient, ApiError
from app.audiosocket import AudioSocket, AudioSocketClosed
from app.config import config
from app.dialog import SurveyDialog

logging.basicConfig(
    level=getattr(logging, config.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("voice-agent")

_active_calls = 0


async def handle_connection(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Atiende una llamada de punta a punta."""
    global _active_calls

    socket = AudioSocket(reader, writer)
    api = ApiClient()
    _active_calls += 1
    log.info("Conexión desde %s (%d activas)", socket.peer, _active_calls)

    try:
        session_uuid = await socket.read_uuid()
        log.info("Sesión %s", session_uuid)

        try:
            script = await api.get_script(session_uuid)
        except ApiError as exc:
            # Sin guion no hay nada que decir: cortamos en vez de dejar al
            # cliente escuchando silencio.
            log.error("No se pudo traer el guion de %s: %s", session_uuid, exc)
            await socket.hangup()
            return

        if not script.questions:
            log.error("La campaña %s no tiene preguntas activas", script.campaign_id)
            await socket.hangup()
            return

        # El caché del TTS es global: la primera llamada de cada campaña paga la
        # síntesis y las siguientes reutilizan.
        await asyncio.to_thread(tts.warm_up, script.all_prompts)

        if not script.demo:
            try:
                await api.session_started(session_uuid)
            except ApiError as exc:
                log.warning("No se pudo marcar la sesión como atendida: %s", exc)

        await SurveyDialog(socket, api, script).run()
        await socket.hangup()

    except AudioSocketClosed as exc:
        log.info("Sesión terminada: %s", exc)
    except Exception:  # noqa: BLE001 - una llamada que falla no baja el servicio
        log.exception("Error inesperado atendiendo %s", socket.peer)
    finally:
        _active_calls -= 1
        await api.aclose()
        await socket.close()
        log.info("Conexión cerrada (%d activas)", _active_calls)


async def main() -> None:
    log.info("Cargando modelos...")
    await asyncio.to_thread(tts.load_voice)
    await asyncio.to_thread(stt.load_model)

    server = await asyncio.start_server(
        handle_connection, config.listen_host, config.listen_port
    )

    addresses = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("AudioSocket escuchando en %s", addresses)
    log.info(
        "STT=%s/%s  TTS=%s  API=%s",
        config.whisper_model, config.whisper_device,
        config.piper_voice, config.api_base_url,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # Windows

    async with server:
        await stop.wait()

    log.info("Apagando (%d llamadas activas)", _active_calls)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
