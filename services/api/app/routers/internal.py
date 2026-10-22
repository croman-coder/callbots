"""Contrato entre la API y el voice-agent.

El voice-agent no toca la base de datos ni Bitrix: solo habla por acá. Toda la
lógica de negocio (interpretar respuestas, decidir si repreguntar, disparar el
análisis) vive en la API, así hay un único lugar donde cambiarla.

Secuencia de una llamada:

    GET  /internal/sessions/{uuid}/script    -> guion a leer
    POST /internal/sessions/{uuid}/started   -> el cliente atendió
    POST /internal/sessions/{uuid}/answers   -> una por pregunta (N veces)
    POST /internal/sessions/{uuid}/finished  -> colgó; dispara análisis
"""

from __future__ import annotations

import logging
import re
import unicodedata
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import acuse, conocimiento, datos_cliente, entrantes, fuentes, pronunciaciones, recepcion, vocabulario
from app.db import get_db
from app.deps import require_internal_token
from app.models import (
    Answer,
    CallAttempt,
    CallOutcome,
    Campaign,
    Caso,
    Question,
    SurveyTarget,
    TargetStatus,
)
from app.schemas import (
    AcuseIn,
    AnswerIn,
    AnswerResult,
    GreetingIn,
    QuestionOut,
    RecepcionFin,
    RecepcionInicio,
    RecepcionTurno,
    ReplyRequest,
    SessionFinished,
    SessionStarted,
    SurveyScript,
)
from app.services import conversation
from app.services.scoring import detect_baja, detect_ocupado, interpret
from app.services.transferir import pide_persona

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)

# UUID que manda el dialplan en la extensión 9000 (prueba manual desde softphone).
# No persiste nada: sirve para escuchar el guion sin ensuciar los datos.
DEMO_UUID = uuid_mod.UUID("00000000-0000-0000-0000-000000000000")


def es_demo(session_uuid: uuid_mod.UUID) -> bool:
    """Sesiones de prueba, que no guardan nada: 0 es la extensión 9000 (la primera campaña activa)
    y N es la prueba desde el navegador del agente N, aunque esté pausado (ver routers/agente.py)."""
    return 0 <= session_uuid.int < 2**32


def sesion_de_prueba(campaign_id: int) -> uuid_mod.UUID:
    return uuid_mod.UUID(int=campaign_id)


def _load_call(db: Session, session_uuid: uuid_mod.UUID) -> CallAttempt:
    call = db.scalar(
        select(CallAttempt).where(CallAttempt.session_uuid == session_uuid)
    )
    if call is None:
        raise HTTPException(404, f"No hay llamada para la sesión {session_uuid}")
    return call


# La dice el agente si el cliente pidió un asesor y la transferencia no se pudo hacer. Va fija acá
# (y también en el voice-agent) para precalentarla con la voz de la campaña.
# Lo que dice el bot si al saludar el cliente contesta que no puede ahora. Igual en el voice-agent (api_client.py).
FRASE_LLAMAR_DESPUES = "No hay problema, lo llamamos en otro momento. Que tenga un buen día."
# La pregunta del saludo, para que Claude sepa a qué volver si el cliente pregunta algo antes de empezar.
PREGUNTA_DEL_SALUDO = "¿Tiene un minuto para unas preguntas cortas?"

FRASE_TRANSFERENCIA_FALLIDA = (
    "Disculpe, ahora no puedo comunicarlo con un asesor. Tomo nota para que lo llamen. "
    "Si le parece, sigamos con la encuesta."
)


def _opciones_de_llamada(campaign: Campaign, datos: dict[str, str] | None = None) -> dict:
    """Buzón de voz y transferencia, para el guion (igual en una llamada real y en una de prueba)."""
    datos = datos or {}
    return {
        "voicemail_action": campaign.voicemail_action or None,
        "voicemail_script": datos_cliente.render(campaign.voicemail_script or "", datos),
        "transfer_enabled": bool(campaign.transfer_number),
        "transfer_script": datos_cliente.render(campaign.transfer_script or "", datos),
    }


def _textos_del_guion(campaign: Campaign, questions: list[Question], datos: dict[str, str]) -> dict:
    """Saludo, cierre, frases y preguntas con los datos del cliente puestos ({{modelo}}, {{sucursal}}…)."""
    r = lambda t: datos_cliente.render(t, datos)  # noqa: E731
    return {
        "intro_script": r(campaign.intro_script),
        "outro_script": r(campaign.outro_script),
        "fallback_script": r(campaign.fallback_script),
        "optout_script": r(campaign.optout_script),
        "questions": [QuestionOut.model_validate(q).model_copy(update={"text": r(q.text)}) for q in questions],
    }


def _datos_de_prueba(db: Session, campaign: Campaign) -> dict[str, str]:
    """En una prueba no hay cliente: se usan los datos del último destinatario cargado (sin su nombre, que en la
    prueba no se dice), así se escucha cómo suena el guion con datos de verdad."""
    ultimo = db.scalar(
        select(SurveyTarget).where(SurveyTarget.campaign_id == campaign.id).order_by(SurveyTarget.id.desc()).limit(1)
    )
    datos = datos_cliente.valores(ultimo, campaign)
    for clave in ("nombre", "nombre_completo"):
        datos.pop(clave, None)
    return datos


def _active_questions(campaign: Campaign) -> list[Question]:
    return [q for q in campaign.questions if q.is_active]


# ---------------------------------------------------------------------------
@router.get("/sessions/{session_uuid}/script", response_model=SurveyScript)
def get_script(session_uuid: uuid_mod.UUID, db: Session = Depends(get_db)) -> SurveyScript:
    """Devuelve el guion completo. Es lo primero que pide el voice-agent."""

    # --- modo demo (extensión 9000) ---
    if es_demo(session_uuid):
        if session_uuid.int == 0:
            campaign = db.scalar(
                select(Campaign)
                .where(Campaign.is_active.is_(True))
                .order_by(Campaign.id)
            )
            if campaign is None:
                raise HTTPException(404, "No hay ninguna campaña activa para demostrar")
        else:
            campaign = db.get(Campaign, session_uuid.int)
            if campaign is None:
                raise HTTPException(404, "Ese agente no existe")

        return SurveyScript(
            session_uuid=session_uuid,
            call_id=None,
            demo=True,
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            contact_name=None,
            voice_speed=campaign.voice_speed,
            voice_pitch=campaign.voice_pitch,
            voice_expressiveness=campaign.voice_expressiveness,
            voice_volume=campaign.voice_volume,
            voice_profile_id=campaign.voice_profile_id,
            sonido_fondo=campaign.sonido_fondo, teclado=bool(campaign.teclado),
            **_opciones_de_llamada(campaign, (datos := _datos_de_prueba(db, campaign))),
            **_textos_del_guion(campaign, _active_questions(campaign), datos),
            pronunciaciones=pronunciaciones.lista(db),
            vocabulario=_vocabulario(db, campaign),
            **_modo(campaign),
        )

    # --- llamada entrante: el caso lo abrió Asterisk al entrar (ver /internal/entrantes) ---
    entrante = entrantes.caso_de_sesion(db, session_uuid)
    if entrante is not None:
        campaign = entrante.campaign
        return SurveyScript(
            session_uuid=session_uuid,
            call_id=None,
            demo=False,
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            contact_name=None,
            voice_speed=campaign.voice_speed,
            voice_pitch=campaign.voice_pitch,
            voice_expressiveness=campaign.voice_expressiveness,
            voice_volume=campaign.voice_volume,
            voice_profile_id=campaign.voice_profile_id,
            sonido_fondo=campaign.sonido_fondo, teclado=bool(campaign.teclado),
            **_opciones_de_llamada(campaign, {}),
            **_textos_del_guion(campaign, [], {}),
            pronunciaciones=pronunciaciones.lista(db),
            vocabulario=_vocabulario(db, campaign),
            **_modo(campaign),
        )

    # --- llamada saliente ---
    call = _load_call(db, session_uuid)
    target = call.target
    campaign = target.campaign

    questions = _active_questions(campaign)
    if not questions and not recepcion.es_recepcion(campaign):
        raise HTTPException(409, f"La campaña {campaign.id} no tiene preguntas activas")

    # Primer nombre solamente: "Buenos días Juan" suena mejor que el nombre completo
    first_name = (target.contact_name or "").split(" ")[0] or None

    return SurveyScript(
        session_uuid=session_uuid,
        call_id=call.id,
        demo=False,
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        contact_name=first_name,
        voice_speed=campaign.voice_speed,
        voice_pitch=campaign.voice_pitch,
        voice_expressiveness=campaign.voice_expressiveness,
        voice_volume=campaign.voice_volume,
        voice_profile_id=campaign.voice_profile_id,
            sonido_fondo=campaign.sonido_fondo, teclado=bool(campaign.teclado),
        **_opciones_de_llamada(campaign, (datos := datos_cliente.valores(target, campaign))),
        **_textos_del_guion(campaign, questions, datos),
        pronunciaciones=pronunciaciones.lista(db),
        vocabulario=_vocabulario(db, campaign),
        **_modo(campaign),
    )


def _vocabulario(db: Session, campaign: Campaign) -> list[str]:
    """Lo que Whisper tiene que reconocer: el vocabulario del agente, lo de los sistemas que consulta (si es de
    recepción) y el diccionario de pronunciación."""
    extra = fuentes.vocabulario(db, campaign) if recepcion.es_recepcion(campaign) else []
    return vocabulario.para_whisper(campaign.vocabulario, pronunciaciones.lista(db), extra)


def _modo(campaign: Campaign) -> dict:
    if not recepcion.es_recepcion(campaign):
        return {"modo": "encuesta"}
    return {
        "modo": "recepcion",
        "frases": {
            "repetir": recepcion.FRASE_REPETIR,
            "registrado": recepcion.FRASE_REGISTRADO,
            "sigue_ahi": recepcion.FRASE_SIGUE_AHI,
            "sin_respuesta": recepcion.FRASE_SIN_RESPUESTA,
            "tiempo": recepcion.FRASE_TIEMPO,
        },
    }


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/started")
def session_started(
    session_uuid: uuid_mod.UUID,
    payload: SessionStarted,
    db: Session = Depends(get_db),
) -> dict:
    """El AudioSocket se conectó: el cliente atendió de verdad."""
    if es_demo(session_uuid):
        return {"demo": True}
    if entrantes.caso_de_sesion(db, session_uuid) is not None:
        return {"entrante": True}

    call = _load_call(db, session_uuid)
    call.answered_at = payload.answered_at or datetime.now(timezone.utc)
    db.commit()

    log.info("Sesión %s atendida (call_id=%s)", session_uuid, call.id)
    return {"call_id": call.id}


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/answers", response_model=AnswerResult)
def submit_answer(
    session_uuid: uuid_mod.UUID,
    payload: AnswerIn,
    db: Session = Depends(get_db),
) -> AnswerResult:
    """Guarda una respuesta y le dice al agente si puede avanzar.

    El agente no interpreta nada: manda la transcripción y acata la respuesta.
    """
    question = db.get(Question, payload.question_id)
    if question is None:
        raise HTTPException(404, f"Pregunta {payload.question_id} inexistente")

    result = interpret(payload.transcript, question.qtype)

    # Repreguntar solo si no se entendió y todavía quedan reintentos
    should_retry = (
        not result.understood
        and not result.is_optout
        and payload.retries_used < question.max_retries
    )

    # Pidió una persona y hay a quién pasarle la llamada. Pedir no seguir (optout) manda: quien dice
    # «no quiero hablar con nadie» no quiere un asesor.
    quiere_asesor = (
        not result.is_optout
        and bool(question.campaign.transfer_number)
        and pide_persona(payload.transcript)
    )

    if es_demo(session_uuid):
        return AnswerResult(
            understood=result.understood,
            value=result.value,
            is_optout=result.is_optout,
            is_silence=result.is_silence,
            should_retry=should_retry,
            saved=False,
            wants_transfer=quiere_asesor,
        )

    call = _load_call(db, session_uuid)

    # Se guarda siempre, también cuando va a repreguntar. La fila se pisa con
    # cada intento, así que la última sigue siendo la buena — pero si el
    # cliente corta justo durante la repregunta, antes se perdía sin dejar
    # rastro lo que había llegado a decir. Para auditar una interpretación
    # dudosa hace falta esa transcripción.
    existing = db.scalar(
        select(Answer).where(
            Answer.call_id == call.id, Answer.question_id == question.id
        )
    )
    answer = existing or Answer(call_id=call.id, question_id=question.id)
    answer.transcript = payload.transcript
    answer.asr_confidence = payload.asr_confidence
    answer.audio_path = payload.audio_path
    answer.duration_seconds = payload.duration_seconds
    answer.retries_used = payload.retries_used
    answer.value_numeric = result.value
    answer.value_source = "rules" if result.understood else None

    if existing is None:
        db.add(answer)

    # Cortar la encuesta puede ser «no me llamen más» (baja) o «ahora no puedo» (ocupado). Solo la baja deja de
    # llamarlo: el ocupado termina como parcial y, si no llegó a contestar nada, se lo vuelve a llamar.
    if result.is_optout and detect_baja(payload.transcript):
        call.target.status = TargetStatus.OPTED_OUT
        log.info("Sesión %s: el cliente pidió no ser contactado", session_uuid)
    elif result.is_optout:
        log.info("Sesión %s: el cliente no puede ahora; se lo llama en otro momento", session_uuid)

    db.commit()

    return AnswerResult(
        understood=result.understood,
        value=result.value,
        is_optout=result.is_optout,
        is_silence=result.is_silence,
        should_retry=should_retry,
        saved=True,
        wants_transfer=quiere_asesor,
    )


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/finished")
def session_finished(
    session_uuid: uuid_mod.UUID,
    payload: SessionFinished,
    db: Session = Depends(get_db),
) -> dict:
    """Cierra la llamada y encola el análisis."""
    if es_demo(session_uuid):
        return {"demo": True}

    call = _load_call(db, session_uuid)
    now = datetime.now(timezone.utc)

    call.ended_at = now
    call.duration_seconds = int((now - (call.started_at or now)).total_seconds())
    call.hangup_cause = payload.hangup_cause
    call.recording_path = payload.recording_path

    # Los conteos se derivan de lo que quedó guardado, no de lo que dice el
    # agente. Si el agente muere a mitad del reporte, o repregunta y la llamada
    # se corta, su número y las filas de `answers` dejan de coincidir — y el
    # que se usa después para calcular tasas es este.
    contadas = db.execute(
        select(
            func.count(Answer.id),
            func.count(Answer.value_numeric),
        ).where(Answer.call_id == call.id)
    ).one()
    call.questions_asked = max(payload.questions_asked, contadas[0])
    call.questions_answered = contadas[1]

    # Si el cliente pidió salir, respetamos eso por encima del outcome reportado
    if call.target.status is TargetStatus.OPTED_OUT:
        call.outcome = CallOutcome.PARTIAL
    else:
        call.outcome = payload.outcome

    # Un contestador atiende, pero no es un cliente: no cuenta como llamada atendida. Sin esto, la tasa
    # de conexión, «Conectadas» y la mejor hora para llamar se inflan con máquinas.
    if call.outcome is CallOutcome.VOICEMAIL:
        call.answered_at = None

    db.commit()

    log.info(
        "Sesión %s cerrada: %s (%s/%s preguntas, %ss)",
        session_uuid,
        call.outcome.value if call.outcome else "?",
        payload.questions_answered,
        payload.questions_asked,
        call.duration_seconds,
    )

    # Import local: evita el ciclo routers -> tasks -> services -> routers
    from app.scheduler.tasks import finalize_call

    if call.target.status is not TargetStatus.OPTED_OUT:
        finalize_call.delay(call.id)

    return {"call_id": call.id, "outcome": call.outcome.value if call.outcome else None}


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/transfer")
def transfer(session_uuid: uuid_mod.UUID, db: Session = Depends(get_db)) -> dict:
    """El cliente pidió un asesor: le avisa al dialplan a quién marcar cuando el audio termine.

    El voice-agent maneja el audio, no el canal. Acá se deja en Asterisk (por ARI) una variable global
    TRANSFER_<sesión> con el destino; el agente cierra el AudioSocket y el dialplan, al seguir, la lee y
    marca al asesor uniendo las dos puntas. Devuelve `ok: false` (nunca error) si no se pudo: del otro
    lado hay una persona esperando y el agente decide qué decirle.
    """
    if es_demo(session_uuid):
        return {"ok": True, "demo": True}

    entrante = entrantes.caso_de_sesion(db, session_uuid)
    campaign = entrante.campaign if entrante is not None else _load_call(db, session_uuid).target.campaign
    if not campaign.transfer_number:
        return {"ok": False, "motivo": "el agente no tiene un teléfono de asesor"}

    from app.services.asterisk_ari import AriClient, AriError

    try:
        with AriClient(timeout=5.0) as ari:
            endpoint = ari.build_endpoint(campaign.transfer_number)
            ari.set_global(f"TRANSFER_{session_uuid}", endpoint)
    except AriError as exc:
        log.error("Sesión %s: no se pudo preparar la transferencia: %s", session_uuid, exc)
        return {"ok": False, "motivo": "Asterisk no respondió"}

    log.info("Sesión %s: transferida a %s", session_uuid, campaign.transfer_number)
    return {"ok": True}


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/acuse")
async def acuse_de_respuesta(session_uuid: uuid_mod.UUID, body: AcuseIn, db: Session = Depends(get_db)) -> dict:
    """Lo que dice el agente después de una respuesta, antes de la próxima pregunta: muestra que lo escuchó.

    {"acuse": "...", "banda": alta|media|baja|neutra|abierta}. Con solo el puntaje, una frase fija al instante; si
    contó algo más, Claude la retoma (con tope de tiempo). Ver app/acuse.py. No guarda nada.
    """
    question = db.get(Question, body.question_id)
    if question is None:
        raise HTTPException(404, f"Pregunta {body.question_id} inexistente")
    valor = interpret(body.transcript, question.qtype).value
    texto, banda = await acuse.acuse(question.text, question.qtype, body.transcript, valor, question.position)
    log.info("Sesión %s P%s: acuse %s -> %r", session_uuid, question.position, banda, texto[:70])
    return {"acuse": texto, "banda": banda}


@router.post("/sessions/{session_uuid}/reply")
async def conversational_reply(
    session_uuid: uuid_mod.UUID,
    body: ReplyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Qué contestarle al cliente cuando dijo algo que no es una respuesta.

    Solo se llama en la rama de excepción: si el cliente contestó bien, el
    voice-agent sigue de largo sin pasar por acá y sin pagar la latencia.

    Devuelve `{"reply": null}` cuando Claude no está configurado, tarda de más
    o falla. El voice-agent usa entonces la frase fija de la campaña.
    """
    target = None
    if es_demo(session_uuid):
        # La prueba de un agente lleva su id en la sesión: se responde con SUS instrucciones y SU conocimiento.
        campaign = (db.get(Campaign, session_uuid.int) if session_uuid.int else None) or db.scalar(
            select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.id)
        )
    else:
        call = _load_call(db, session_uuid)
        target = call.target
        campaign = target.campaign if target else None

    texto = await conversation.reply(
        pregunta=body.question_text,
        dijo=body.transcript,
        intento=body.retries_used,
        extra_prompt=getattr(campaign, "conversation_prompt", None),
        conocimiento="\n\n".join(b for b in (
            conocimiento.para_respuesta(db, campaign, f"{body.question_text} {body.transcript}"),
            datos_cliente.para_claude(datos_cliente.valores(target, campaign)) if target is not None else "",
        ) if b),
    )
    if texto:
        log.info("[%s] Respuesta conversacional: %r", session_uuid, texto[:80])

    return {"reply": texto}


_PREGUNTA = re.compile(
    r"\?|^(quien|que|de donde|para que|por que|como|cual|cuanto|con quien|de parte de)\b"
    r"|\b(quien habla|quien es|de parte de quien|de donde (me )?llam|para que es|que (es|quiere|necesita|desea))\b"
)


@router.post("/sessions/{session_uuid}/greeting")
async def respuesta_al_saludo(session_uuid: uuid_mod.UUID, body: GreetingIn, db: Session = Depends(get_db)) -> dict:
    """Qué hacer con lo que contestó el cliente al «¿tiene un minutito?» del saludo.

    `accion`: «seguir» (dijo que sí, saludó o algo que no cambia nada), «baja» (no quiere que lo llamen: queda
    marcado), «reprogramar» (no puede ahora: se lo llama en otro momento), «asesor» (pidió una persona y el agente
    tiene a quién pasarle) o «responder» (preguntó algo: `respuesta` es lo que contesta Claude, que vuelve a pedir
    el minuto). Reglas primero, sin LLM: solo una pregunta paga la espera de Claude.
    """
    texto = (body.transcript or "").strip()
    demo = es_demo(session_uuid)
    if demo:
        campaign = db.get(Campaign, session_uuid.int) or db.scalar(
            select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.id)
        )
        call = None
    else:
        call = _load_call(db, session_uuid)
        campaign = call.target.campaign if call.target else None

    if not texto:
        return {"accion": "seguir"}
    if detect_baja(texto):
        if call is not None:
            call.target.status = TargetStatus.OPTED_OUT
            db.commit()
        log.info("[%s] Al saludar pidió que no lo llamen", session_uuid)
        return {"accion": "baja"}
    if campaign is not None and campaign.transfer_number and pide_persona(texto):
        return {"accion": "asesor"}
    if detect_ocupado(texto):
        log.info("[%s] Al saludar dijo que no puede ahora", session_uuid)
        return {"accion": "reprogramar"}
    if _PREGUNTA.search(_plano_pregunta(texto)) or "?" in texto:
        respuesta = await conversation.reply(
            pregunta=PREGUNTA_DEL_SALUDO, dijo=texto, intento=0,
            extra_prompt=getattr(campaign, "conversation_prompt", None),
            conocimiento=conocimiento.para_respuesta(db, campaign, texto),
        )
        if respuesta:
            return {"accion": "responder", "respuesta": respuesta}
    return {"accion": "seguir"}


def _plano_pregunta(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ\s]", " ", sin_tildes)).strip()


def con_nombre(texto: str, nombre: str | None) -> str:
    """Lo mismo que hace el voice-agent al decir el guion (`_personalize` en dialog.py): tienen que
    dar el mismo texto, porque el audio precalentado se busca por el texto exacto."""
    if "{nombre}" not in texto:
        return texto
    if nombre:
        return texto.replace("{nombre}", nombre)
    return re.sub(r",?[ ]*\{nombre\}", "", texto).replace("  ", " ")


# Cuántos nombres distintos se precalientan por campaña. Cada uno es una síntesis de decenas de
# segundos en CPU; los que no entren se dicen con Piper la primera vez y quedan cacheados.
MAX_NOMBRES_PRECALENTADOS = 40


@router.get("/prompts")
def all_prompts(db: Session = Depends(get_db)) -> dict:
    """Todo el texto fijo de las campañas activas, para precalentar el TTS.

    El voice-agent lo pide al arrancar y sintetiza todo antes de atender la
    primera llamada. Con voz clonada cada frase puede tardar decenas de
    segundos: hacerlo en medio de una llamada dejaría al cliente escuchando
    silencio.
    """
    campaigns = db.scalars(
        select(Campaign)
        .options(selectinload(Campaign.questions))
        # Los agentes nuevos (pausados) también: quien los prueba desde el navegador los tiene que
        # escuchar con la voz buena, no con la de respaldo.
        .where(
            or_(
                Campaign.is_active.is_(True),
                Campaign.updated_at >= datetime.now(timezone.utc) - timedelta(days=3),
            )
        )
    ).all()

    # Se agrupa por campaña porque cada una tiene su propia modulación de voz:
    # la misma frase con otra velocidad o tono es otro audio, y cachearla con
    # los parámetros equivocados haría que el precalentado no sirva de nada.
    grupos = []
    todos: list[str] = []

    for campaign in campaigns:
        # Primeros nombres de quienes van a ser llamados: el saludo lleva el nombre, y una frase
        # con un nombre que nunca se sintetizó sale con Piper en medio de la voz buena.
        proximos = db.scalars(
            select(SurveyTarget)
            .where(
                SurveyTarget.campaign_id == campaign.id,
                SurveyTarget.status.in_(
                    [TargetStatus.PENDING, TargetStatus.SCHEDULED, TargetStatus.QUEUED]
                ),
            )
            .order_by(SurveyTarget.id)
            .limit(400)
        ).all()
        nombres = list(
            dict.fromkeys(t.contact_name.split(" ")[0] for t in proximos if t.contact_name and t.contact_name.strip())
        )[:MAX_NOMBRES_PRECALENTADOS]
        # Con datos del cliente ({{modelo}}…) cada destinatario dice otra frase: se precalientan las de los próximos.
        datos_proximos = [datos_cliente.valores(t, campaign) for t in proximos[:MAX_NOMBRES_PRECALENTADOS]]

        textos = [
            campaign.intro_script,
            campaign.outro_script,
            campaign.fallback_script,
            campaign.optout_script,
        ]
        textos += [q.text for q in campaign.questions if q.is_active]
        textos += [q.texto_sin_acuse for q in campaign.questions if q.is_active and not recepcion.es_recepcion(campaign)]
        if not recepcion.es_recepcion(campaign):
            textos += acuse.todas_las_fijas()
        textos.append(FRASE_LLAMAR_DESPUES)
        if recepcion.es_recepcion(campaign):
            textos += list(recepcion.FRASES_FIJAS)
        # Lo que se dice si atiende un contestador o si el cliente pide un asesor: tiene que salir con
        # la voz buena y sin espera, igual que el resto del guion.
        if campaign.voicemail_action == "mensaje":
            textos.append(campaign.voicemail_script)
        if campaign.transfer_number:
            textos += [campaign.transfer_script, FRASE_TRANSFERENCIA_FALLIDA]
        variantes: list[str] = []
        for texto in textos:
            if texto and "{{" in texto:
                # Cada cliente con sus datos (y su nombre, si el texto también lo lleva), más la versión sin datos.
                for datos in [{}, *datos_proximos]:
                    con_datos = datos_cliente.render(texto, datos)
                    variantes.append(con_nombre(con_datos, datos.get("nombre")) if "{nombre}" in con_datos else con_datos)
            elif texto and "{nombre}" in texto:
                variantes += [con_nombre(texto, None)] + [con_nombre(texto, n) for n in nombres]
            else:
                variantes.append(texto)
        limpios = [t.strip() for t in dict.fromkeys(variantes) if t and t.strip()]
        todos += limpios

        grupos.append(
            {
                "voice": {
                    "speed": campaign.voice_speed,
                    "pitch": campaign.voice_pitch,
                    "expressiveness": campaign.voice_expressiveness,
                    "volume": campaign.voice_volume,
                    "profile_id": campaign.voice_profile_id or "",
                },
                "texts": limpios,
            }
        )

    # `texts` se mantiene por compatibilidad con agentes viejos.
    unique = [t for t in dict.fromkeys(todos)]

    return {"campaigns": len(campaigns), "texts": unique, "grupos": grupos, "pronunciaciones": pronunciaciones.lista(db)}


# ---------------------------------------------------------------------------
# Recepción: el agente atiende y conversa turno a turno (app/recepcion.py)
def _campaign_de_sesion(db: Session, session_uuid: uuid_mod.UUID) -> Campaign:
    if es_demo(session_uuid):
        campaign = (db.get(Campaign, session_uuid.int) if session_uuid.int else None) or db.scalar(
            select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.id)
        )
    elif (entrante := entrantes.caso_de_sesion(db, session_uuid)) is not None:
        campaign = entrante.campaign
    else:
        target = _load_call(db, session_uuid).target
        campaign = target.campaign if target else None
    if campaign is None:
        raise HTTPException(404, "No hay agente para esta sesión")
    return campaign


def _caso(db: Session, caso_id: int) -> Caso:
    caso = db.get(Caso, caso_id)
    if caso is None:
        raise HTTPException(404, f"No existe el caso {caso_id}")
    return caso


def conocimiento_del_caso(db: Session, caso: Caso, dijo: str) -> str:
    return conocimiento.para_respuesta(db, caso.campaign, f"{caso.resumen or ''} {dijo}".strip())


@router.post("/entrantes", response_class=PlainTextResponse)
def llamada_entrante(de: str = Form(""), a: str = Form(""), db: Session = Depends(get_db)) -> PlainTextResponse:
    """Asterisk avisa que entró una llamada (contexto `callbot-entrante`, por CURL). «ok:<sesión>» = la atiende un
    agente de recepción: el caso queda abierto con esa sesión, que es la que Asterisk le pasa al AudioSocket. «no» =
    ningún agente atiende ese número y Asterisk la pasa al número de respaldo o da ocupado. La sesión la crea la API
    porque el Asterisk 18 de la imagen no tiene UUID(). Texto plano: es lo que lee el dialplan."""
    sesion = uuid_mod.uuid4()
    caso = entrantes.abrir(db, sesion, de, a)
    if caso is None:
        log.warning("Llamada entrante de %s al %s: ningún agente atiende ese número", de or "?", a or "?")
        return PlainTextResponse("no")
    db.commit()
    log.info("Llamada entrante de %s al %s: caso %s (%s)", de or "?", a or "?", caso.id, caso.campaign.name)
    return PlainTextResponse(f"ok:{sesion}")


@router.post("/sessions/{session_uuid}/recepcion")
def recepcion_inicio(session_uuid: uuid_mod.UUID, body: RecepcionInicio, db: Session = Depends(get_db)) -> dict:
    """Abre el caso de esta llamada, con el saludo que ya dijo el agente como primera línea."""
    campaign = _campaign_de_sesion(db, session_uuid)
    if not recepcion.es_recepcion(campaign):
        raise HTTPException(409, "Este agente no es de recepción")
    if (entrante := entrantes.caso_de_sesion(db, session_uuid)) is not None:
        return {"caso_id": entrante.id}  # llamada entrante: el caso ya existe, con el saludo
    demo = es_demo(session_uuid)
    caso = recepcion.nuevo(
        campaign, "prueba" if demo else "telefono",
        session_uuid=None if demo else session_uuid, telefono=(body.telefono or "").strip()[:30] or None,
    )
    recepcion.decir(caso, "agente", con_nombre(datos_cliente.render(campaign.intro_script, {}), None))
    db.add(caso)
    db.commit()
    log.info("[%s] Recepción: caso %s abierto (%s)", session_uuid, caso.id, caso.origen)
    return {"caso_id": caso.id}


@router.post("/casos/{caso_id}/turno")
async def recepcion_turno(caso_id: int, body: RecepcionTurno, db: Session = Depends(get_db)) -> dict:
    """Lo que el agente contesta y qué hace después: {"decir", "accion": seguir|derivar|transferir|terminar}."""
    caso = _caso(db, caso_id)
    respuesta = await recepcion.turno(
        caso, body.texto,
        conocimiento=conocimiento_del_caso(db, caso, body.texto),
        transferir=bool(caso.campaign.transfer_number) and caso.origen == "telefono",
        consultas=fuentes.bloque(caso),
    )
    db.commit()
    log.info("Caso %s: %s -> %r", caso.id, respuesta["accion"], respuesta["decir"][:70])
    return respuesta


@router.post("/casos/{caso_id}/continuar")
async def recepcion_continuar(caso_id: int, db: Session = Depends(get_db)) -> dict:
    """Después de «consultar»: corre la consulta en vivo (Cotizador) y devuelve lo que el agente contesta con el dato.
    El voice-agent la pide apenas recibe «consultar», mientras dice «ya lo busco», así la espera se tapa sola."""
    caso = _caso(db, caso_id)
    respuesta = await recepcion.continuar(
        caso, fuentes.consultor(caso),
        conocimiento=conocimiento_del_caso(db, caso, ""),
        transferir=bool(caso.campaign.transfer_number) and caso.origen == "telefono",
    )
    db.commit()
    log.info("Caso %s (consulta): %s -> %r", caso.id, respuesta["accion"], respuesta["decir"][:70])
    return respuesta


@router.post("/casos/{caso_id}/fin")
def recepcion_fin(caso_id: int, body: RecepcionFin, db: Session = Depends(get_db)) -> dict:
    """Cortó o se despidió: lo que quedó a medias se deriva igual, para que nadie se quede sin respuesta."""
    caso = _caso(db, caso_id)
    recepcion.terminar_llamada(caso, body.causa)
    db.commit()
    avisar_caso(caso)
    return {"caso_id": caso.id, "estado": caso.estado}


def avisar_caso(caso: Caso) -> None:
    """Avisa el caso derivado: a los webhooks del agente y, si el área tiene WhatsApp, a su responsable. Se llama
    después del commit. Las pruebas no avisan a nadie."""
    if caso.estado != "abierto" or caso.origen in recepcion.DE_PRUEBA:
        return
    from app import avisos

    avisos.por_caso_derivado(caso.id)
    if not any(w.get("activo", True) for w in caso.campaign.webhooks or []):
        return
    from app.scheduler.tasks import enviar_caso

    enviar_caso.delay(caso.id)


@router.get("/targets/{session_uuid}/context")
def get_context(session_uuid: uuid_mod.UUID, db: Session = Depends(get_db)) -> dict:
    """Datos del registro de Bitrix, por si el guion los menciona."""
    if es_demo(session_uuid):
        return {"demo": True}

    call = _load_call(db, session_uuid)
    target: SurveyTarget = call.target

    return {
        "contact_name": target.contact_name,
        "bitrix_entity_id": target.bitrix_entity_id,
        "bitrix_title": target.bitrix_title,
        "trigger_at": target.trigger_at.isoformat() if target.trigger_at else None,
        "invoice_date": target.invoice_date.isoformat() if target.invoice_date else None,
        "delivery_date": target.delivery_date.isoformat() if target.delivery_date else None,
        "attempt": call.attempt_number,
    }
