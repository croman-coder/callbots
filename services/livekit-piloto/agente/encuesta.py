"""Piloto: la encuesta de satisfacción de posventa con LiveKit Agents.

Mismas piezas que Callbot (el Whisper de app/stt.py, la voz de Voicebox, Claude Haiku) puestas en el framework de LiveKit, que se
ocupa del audio WebRTC, de cuándo termina de hablar la persona (VAD + modelo de fin de turno local), de las interrupciones y de
mandar al LLM y a la voz de a frases. Sirve para comparar contra el voice-agent actual; no toca la base ni a Bitrix.

Optimizado el 23/11/2026 (mediana de 4,9 s por turno en la primera versión): el guion es una máquina de estados con las reglas de Callbot
(`flujo.py`, sin herramientas de Claude: una llamada al LLM por turno y no tres), el prompt del sistema no cambia entre turnos (la
directiva de cada turno va pegada a lo que dijo la persona) y el fin de turno se ajusta con LK_TURNO / LK_MIN_DELAY. LK_ACUSE=fijo
dice los acuses y las preguntas del camino feliz sin pasar por Claude.

    python -m agente.encuesta start
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    JobExecutorType,
    JobProcess,
    MetricsCollectedEvent,
    TurnHandlingOptions,
    cli,
    inference,
    metrics,
    room_io,
)
from livekit.agents.inference import TurnDetector
from livekit.agents.llm import ChatContext, ChatMessage
from livekit.plugins import anthropic

# Se importa acá, en el hilo principal: el import perezoso de transformers desde varios hilos a la vez falla («cannot import name»).
from transformers import WhisperForConditionalGeneration, WhisperProcessor  # noqa: F401

from app import stt as nucleo

from .callbot_stt import WhisperCallbot
from .callbot_tts import VoiceboxTTS
from .flujo import SALUDO, Turnos
from .muletillas import Muletillas, configuracion, preparar_clips, preparar_fondo

load_dotenv()
log = logging.getLogger("piloto")

# El prompt del sistema es el mismo en todos los turnos (el flujo lo lleva flujo.py): así Claude lo recibe siempre igual y es lo que se
# puede cachear. Lo que cambia por turno va en la directiva pegada al mensaje de la persona.
INSTRUCCIONES = """Sos el asistente telefónico del taller de Santa Rosa y hacés una encuesta de satisfacción corta a un cliente que estuvo en el taller.
Hablás en español rioplatense neutro, tratando de «usted» a la persona. Es una llamada: contestá con una o dos frases cortas, sin emojis,
sin markdown, sin listas y sin pasar de unas veinte palabras salvo que haga falta.

En cada mensaje de la persona viene una «Directiva interna»: es lo que tenés que hacer en ese turno. Cumplila al pie de la letra, sin
mencionarla ni decir lo que estás haciendo. Cuando te pide una pregunta «con estas palabras exactas», decila tal cual.

Reglas: nunca nombres a otras marcas ni compares. No inventes datos del taller, de precios ni de plazos: si preguntan algo fuera de la encuesta,
decí que lo consulta el equipo de Calidad y volvé a la pregunta que quedó pendiente. No discutas ni te justifiques."""

# Qué le sopla el agente a Whisper en el próximo turno (ver callbot_stt.py); flujo.py solo conoce los nombres.
PISTAS = {"saludo": nucleo.RESPUESTAS_SALUDO, "numeros": nucleo.NUMEROS, None: None}

# Lo que se decidió en esta llamada, para el JSON que queda guardado y para callbot_stt (la pista).
ESTADO: dict = {"pista": nucleo.RESPUESTAS_SALUDO, "metricas": [], "turnos": [], "decisiones": [], "muletillas": []}


def _ultimo_mensaje_de_la_persona(chat_ctx: ChatContext) -> tuple[int, int, str]:
    """(posición del último mensaje de la persona en el contexto, cuántos hubo en total, su texto)."""
    pos, cuantos, texto = -1, 0, ""
    for i, item in enumerate(chat_ctx.items):
        if getattr(item, "type", "") == "message" and item.role == "user":
            pos, cuantos, texto = i, cuantos + 1, item.text_content or ""
    return pos, cuantos, texto


class Encuesta(Agent):
    """Sin herramientas: el guion lo lleva `Turnos` (flujo.py) y el LLM solo redacta lo que la directiva le pide."""

    def __init__(self, turnos: Turnos, acuse: str) -> None:
        super().__init__(instructions=INSTRUCCIONES)
        self._turnos, self._acuse = turnos, acuse

    async def on_enter(self) -> None:
        await self.session.say(SALUDO, allow_interruptions=True)

    async def llm_node(self, chat_ctx, tools, model_settings):  # noqa: ANN001
        pos, n, texto = _ultimo_mensaje_de_la_persona(chat_ctx)
        if pos < 0:  # no hubo nadie hablando (una respuesta generada a mano): se deja pasar tal cual
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                yield chunk
            return
        decision = self._turnos.decidir(n, texto)
        ESTADO["llm_listo"] = False  # lo prende el primer pedazo de la respuesta (las muletillas «pensando» esperan hasta entonces)
        ESTADO["pista"] = PISTAS[decision.pista]
        ESTADO["cierre"] = decision.cerrar
        sin_llm = self._acuse == "fijo" and decision.fijo is not None
        ESTADO["decisiones"].append({"turno": n, "texto": texto, "anotado": decision.anotado, "modo": "fijo" if sin_llm else "llm"})
        log.info("TURNO %d: %r -> %s (%s)", n, texto, decision.anotado, "texto fijo" if sin_llm else "Claude")
        if sin_llm:
            ESTADO["llm_listo"] = True
            yield decision.fijo
            return
        # La directiva va pegada al mensaje de la persona (en esta copia del contexto, no en la conversación guardada).
        con_directiva = chat_ctx.copy()
        original = chat_ctx.items[pos]
        con_directiva.items[pos] = ChatMessage(
            id=original.id, role="user", content=[texto, f"[Directiva interna, no la menciones: {decision.directiva}]"]
        )
        async for chunk in Agent.default.llm_node(self, con_directiva, tools, model_settings):
            ESTADO["llm_listo"] = True
            yield chunk


_CARGA = threading.Lock()


def precalentar(proc: JobProcess) -> None:
    """El modelo de Whisper en la GPU (uno solo, compartido por los hilos) y el VAD local de LiveKit."""
    with _CARGA:
        nucleo.load_model()
    # El STT de Whisper no es continuo: trabaja sobre cada tramo que corta el VAD. Con el silencio mínimo de LiveKit (0,25 s) una pausa
    # natural parte la respuesta en dos tramos («Más o menos… ocho» se leyó como un 5 y después «menos ocho»). Con 0,5 s casi nunca corta.
    proc.userdata["vad"] = inference.VAD(min_silence_duration=float(os.getenv("LK_VAD_SILENCIO", "0.5")))
    # Muletillas (muletillas.py): un segundo VAD, más sensible, para ver las pausas cortas de quien habla largo, y las palabras ya sintetizadas.
    cfg = configuracion()
    proc.userdata["vad_rapido"] = inference.VAD(min_silence_duration=0.2) if cfg["muletillas"] else None
    proc.userdata["clips"] = {}
    carpeta = Path(os.getenv("LK_DATOS", "/data")) / "muletillas"
    if cfg["muletillas"]:
        try:
            proc.userdata["clips"] = preparar_clips(os.environ["VOICEBOX_URL"], os.environ["VOICEBOX_PROFILE_ID"], carpeta)
        except Exception:  # noqa: BLE001 - sin muletillas el piloto anda igual
            log.exception("no se prepararon las muletillas")
    if cfg["fondo"] == "oficina" and (fondo := preparar_fondo(carpeta, cfg["fondo_rms"])):
        proc.userdata["clips"]["fondo"] = [fondo]


# Hilos y no procesos: el modelo de Whisper vive una sola vez en la GPU. Un solo runner de reserva: el piloto atiende de a una llamada.
server = AgentServer(job_executor_type=JobExecutorType.THREAD, num_idle_processes=1, setup_fnc=precalentar)


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    ctx.log_context_fields = {"room": ctx.room.name}
    ESTADO.update({"pista": nucleo.RESPUESTAS_SALUDO, "cierre": False, "llm_listo": False, "metricas": [], "turnos": [], "decisiones": [], "muletillas": []})
    inicio = time.time()

    # Los tres botones de la latencia, por variable de entorno para medir sin tocar el código (ver README):
    modo_turno = os.getenv("LK_TURNO", "modelo")  # «modelo»: fin de turno por modelo local; «vad»: solo silencio, como Callbot hoy
    min_delay = float(os.getenv("LK_MIN_DELAY", "0.2"))  # espera mínima tras el último ruido antes de dar el turno por terminado
    max_delay = float(os.getenv("LK_MAX_DELAY", "1.2"))
    acuse = os.getenv("LK_ACUSE", "llm")  # «llm»: Claude redacta acuse + pregunta; «fijo»: frases fijas en el camino feliz
    cache = os.getenv("LK_CACHE", "1") != "0"  # caché de prompt de Anthropic (solo aplica si el prompt supera el mínimo del modelo)
    mule_cfg = configuracion()
    config = {**{f"mule_{k}": v for k, v in mule_cfg.items()}, "turno": modo_turno, "vad_silencio": float(os.getenv("LK_VAD_SILENCIO", "0.5")), "min_delay": min_delay, "max_delay": max_delay, "acuse": acuse, "cache": cache,
              "llm": os.getenv("CALLBOT_LLM_MODEL", "claude-haiku-5-5")}
    log.info("configuración: %s", config)

    turnos = TurnHandlingOptions(
        turn_detection=TurnDetector(version="v1-mini") if modo_turno == "modelo" else "vad",
        endpointing={"min_delay": min_delay, "max_delay": max_delay},
        interruption={"mode": "vad"},
        preemptive_generation={"enabled": True},
    )
    session = AgentSession(
        vad=ctx.proc.userdata["vad"],
        stt=WhisperCallbot(ESTADO),
        # Sin `temperature`: claude-haiku-5-5 la rechaza (400 «is deprecated for this model»). Respuestas cortas: tope de 150 tokens.
        llm=anthropic.LLM(model=config["llm"], max_tokens=150, **({"caching": "ephemeral"} if cache else {})),
        tts=VoiceboxTTS(url=os.environ["VOICEBOX_URL"], perfil=os.environ["VOICEBOX_PROFILE_ID"]),
        turn_handling=turnos,
    )
    guion = Turnos()

    @session.on("metrics_collected")
    def _metricas(ev: MetricsCollectedEvent) -> None:
        if ev.metrics.type == "vad_metrics":
            return
        metrics.log_metrics(ev.metrics)
        try:
            ESTADO["metricas"].append(ev.metrics.model_dump() if hasattr(ev.metrics, "model_dump") else dict(vars(ev.metrics)))
        except Exception:  # noqa: BLE001 - las métricas son un extra
            ESTADO["metricas"].append({"type": ev.metrics.type})

    # Para entender por qué una respuesta se pierde o se parte en dos: cuándo la persona empezó y terminó de hablar según el VAD, y qué hacía el agente.
    @session.on("user_state_changed")
    def _estado_persona(ev) -> None:  # noqa: ANN001
        log.info("persona: %s -> %s", ev.old_state, ev.new_state)

    @session.on("agent_state_changed")
    def _estado_agente(ev) -> None:  # noqa: ANN001
        log.info("agente: %s -> %s", ev.old_state, ev.new_state)

    @session.on("user_input_transcribed")
    def _oyo(ev) -> None:  # noqa: ANN001
        if ev.is_final:
            log.info("USUARIO: %r", ev.transcript)
            ESTADO["turnos"].append({"t": round(time.time() - inicio, 2), "rol": "usuario", "texto": ev.transcript})

    @session.on("conversation_item_added")
    def _dijo(ev) -> None:  # noqa: ANN001
        item = ev.item
        if getattr(item, "role", "") == "assistant":
            texto = getattr(item, "text_content", "") or ""
            log.info("AGENTE: %r", texto)
            ESTADO["turnos"].append({"t": round(time.time() - inicio, 2), "rol": "agente", "texto": texto})
            if ESTADO.get("cierre"):  # dijo la despedida: se corta la llamada (sin herramienta, lo decide el guion)
                log.info("la encuesta terminó: %s", guion.estado.resumen())
                session.shutdown(drain=True)

    async def guardar() -> None:
        carpeta = Path(os.getenv("LK_DATOS", "/data")) / "llamadas"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"{ctx.room.name}-{int(inicio)}.json"
        ruta.write_text(json.dumps({"sala": ctx.room.name, "config": config, "resultado": guion.estado.resumen(), **ESTADO},
                                   ensure_ascii=False, indent=1, default=str))
        log.info("llamada guardada en %s", ruta)

    async def colgar() -> None:
        await ctx.delete_room()  # desconecta a la persona (el cliente automático lo toma como el fin de la llamada)

    muletillas = Muletillas(ctx.proc.userdata.get("clips", {}), mule_cfg, ESTADO["muletillas"], inicio, lambda: ESTADO.get("llm_listo", False))

    ctx.add_shutdown_callback(guardar)
    ctx.add_shutdown_callback(muletillas.cerrar)
    ctx.add_shutdown_callback(colgar)

    await session.start(agent=Encuesta(guion, acuse), room=ctx.room, room_options=room_io.RoomOptions(audio_input=room_io.AudioInputOptions()))
    await muletillas.iniciar(ctx, session, ctx.proc.userdata.get("vad_rapido"))


if __name__ == "__main__":
    cli.run_app(server)
