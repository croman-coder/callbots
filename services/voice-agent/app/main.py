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
from app.dialog import SurveyDialog, voz_de

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
        # Prueba desde el navegador (ver routers/agente.py de la API): UUID chico y distinto de cero.
        # Solo ahí se manda la transcripción en vivo.
        socket.eventos = 1 <= session_uuid.int < 2**32

        # Desde acá la lectura del socket corre sola. Es lo que sostiene el
        # reloj de las tramas cuando el otro extremo no manda audio.
        socket.start_pump()

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

        # Red de seguridad: si la campaña se editó después del arranque, lo que falte se
        # genera en segundo plano. NO se espera: con voz de Voicebox una frase tarda decenas
        # de segundos y el cliente ya está en la línea; mientras tanto habla Piper.
        tts.precalentar_en_segundo_plano(script.all_prompts, voz_de(script))

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


async def prewarm_tts(primera_vez: bool = True) -> int:
    """Sintetiza el guion de todas las campañas activas antes de atender llamadas.

    Con voz clonada esto puede tardar minutos la primera vez. Corre acá, con el
    servicio recién levantado y nadie en la línea, y queda cacheado en disco:
    los reinicios siguientes son instantáneos. Se repite cada pocos minutos (ver
    `prewarm_loop`) para que elegir otra voz en el panel no obligue a reiniciar.
    """
    api = ApiClient()
    try:
        grupos = await api.get_all_prompts()
        total = sum(len(g.get("texts", [])) for g in grupos)
        if not total:
            if primera_vez:
                log.info("No hay campañas activas: nada que precalentar")
            return 0

        if primera_vez:
            log.info("Precalentando TTS: %d frases (motor=%s)...", total, config.tts_engine)
        generated = 0
        for grupo in grupos:
            v = grupo.get("voice") or {}
            params = tts.VoiceParams(
                speed=float(v.get("speed", 1.0)),
                pitch=float(v.get("pitch", 1.0)),
                expressiveness=float(v.get("expressiveness", 0.667)),
                volume=float(v.get("volume", 1.0)),
                profile_id=str(v.get("profile_id") or ""),
            )
            generated += await tts.warm_up(grupo.get("texts", []), params)
        if primera_vez or generated:
            log.info("TTS listo: %d frases nuevas, %d ya cacheadas", generated, total - generated)
        return generated
    finally:
        await api.aclose()


PRECALENTADO_CADA_SEGUNDOS = 120


async def prewarm_loop() -> None:
    """Precalienta al arrancar y después cada 2 minutos: lo ya cacheado se saltea sin costo."""
    primera = True
    while True:
        try:
            await prewarm_tts(primera)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - la API puede estar reiniciándose; se reintenta
            log.exception("Falló el precalentado del TTS")
        primera = False
        await asyncio.sleep(PRECALENTADO_CADA_SEGUNDOS)


async def main() -> None:
    log.info("Cargando modelos...")
    await asyncio.to_thread(tts.load_voice)
    await asyncio.to_thread(stt.load_model)

    # No bloquea el arranque: el servidor acepta llamadas mientras precalienta.
    # Lo que todavía no esté cacheado cae al respaldo de Piper por timeout.
    prewarm = asyncio.create_task(prewarm_loop())

    server = await asyncio.start_server(
        handle_connection, config.listen_host, config.listen_port
    )

    addresses = ", ".join(str(s.getsockname()) for s in server.sockets)
    log.info("AudioSocket escuchando en %s", addresses)
    if config.voicebox_url:
        por_defecto = f"perfil {config.voicebox_profile_id[:8]}…" if config.voicebox_profile_id else "Piper por defecto"
        tts_desc = f"voicebox({config.voicebox_engine}; {por_defecto}; la voz se elige por campaña)"
    else:
        tts_desc = f"piper({config.piper_voice})"

    log.info(
        "STT=%s/%s  TTS=%s  API=%s",
        config.whisper_model, config.whisper_device, tts_desc, config.api_base_url,
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

    prewarm.cancel()
    log.info("Apagando (%d llamadas activas)", _active_calls)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
