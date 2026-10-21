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
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.deps import require_internal_token
from app.models import (
    Answer,
    CallAttempt,
    CallOutcome,
    Campaign,
    Question,
    SurveyTarget,
    TargetStatus,
)
from app.schemas import (
    AnswerIn,
    AnswerResult,
    QuestionOut,
    ReplyRequest,
    SessionFinished,
    SessionStarted,
    SurveyScript,
)
from app.services import conversation
from app.services.scoring import interpret
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
FRASE_TRANSFERENCIA_FALLIDA = (
    "Disculpe, ahora no puedo comunicarlo con un asesor. Tomo nota para que lo llamen. "
    "Si le parece, sigamos con la encuesta."
)


def _opciones_de_llamada(campaign: Campaign) -> dict:
    """Buzón de voz y transferencia, para el guion (igual en una llamada real y en una de prueba)."""
    return {
        "voicemail_action": campaign.voicemail_action or None,
        "voicemail_script": campaign.voicemail_script or "",
        "transfer_enabled": bool(campaign.transfer_number),
        "transfer_script": campaign.transfer_script or "",
    }


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
            intro_script=campaign.intro_script,
            outro_script=campaign.outro_script,
            fallback_script=campaign.fallback_script,
            optout_script=campaign.optout_script,
            voice_speed=campaign.voice_speed,
            voice_pitch=campaign.voice_pitch,
            voice_expressiveness=campaign.voice_expressiveness,
            voice_volume=campaign.voice_volume,
            voice_profile_id=campaign.voice_profile_id,
            **_opciones_de_llamada(campaign),
            questions=[QuestionOut.model_validate(q) for q in _active_questions(campaign)],
        )

    # --- llamada real ---
    call = _load_call(db, session_uuid)
    target = call.target
    campaign = target.campaign

    questions = _active_questions(campaign)
    if not questions:
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
        intro_script=campaign.intro_script,
        outro_script=campaign.outro_script,
        fallback_script=campaign.fallback_script,
        optout_script=campaign.optout_script,
        voice_speed=campaign.voice_speed,
        voice_pitch=campaign.voice_pitch,
        voice_expressiveness=campaign.voice_expressiveness,
        voice_volume=campaign.voice_volume,
        voice_profile_id=campaign.voice_profile_id,
        **_opciones_de_llamada(campaign),
        questions=[QuestionOut.model_validate(q) for q in questions],
    )


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

    if result.is_optout:
        call.target.status = TargetStatus.OPTED_OUT
        log.info("Sesión %s: el cliente pidió no ser contactado", session_uuid)

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

    call = _load_call(db, session_uuid)
    campaign = call.target.campaign
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
    if es_demo(session_uuid):
        campaign = db.scalar(
            select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.id)
        )
    else:
        call = _load_call(db, session_uuid)
        campaign = call.target.campaign if call.target else None

    texto = await conversation.reply(
        pregunta=body.question_text,
        dijo=body.transcript,
        intento=body.retries_used,
        extra_prompt=getattr(campaign, "conversation_prompt", None),
    )
    if texto:
        log.info("[%s] Respuesta conversacional: %r", session_uuid, texto[:80])

    return {"reply": texto}


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
            select(SurveyTarget.contact_name)
            .where(
                SurveyTarget.campaign_id == campaign.id,
                SurveyTarget.status.in_(
                    [TargetStatus.PENDING, TargetStatus.SCHEDULED, TargetStatus.QUEUED]
                ),
                SurveyTarget.contact_name.is_not(None),
            )
            .order_by(SurveyTarget.id)
            .limit(400)
        ).all()
        nombres = list(
            dict.fromkeys(n.split(" ")[0] for n in proximos if n and n.strip())
        )[:MAX_NOMBRES_PRECALENTADOS]

        textos = [
            campaign.intro_script,
            campaign.outro_script,
            campaign.fallback_script,
            campaign.optout_script,
        ]
        textos += [q.text for q in campaign.questions if q.is_active]
        # Lo que se dice si atiende un contestador o si el cliente pide un asesor: tiene que salir con
        # la voz buena y sin espera, igual que el resto del guion.
        if campaign.voicemail_action == "mensaje":
            textos.append(campaign.voicemail_script)
        if campaign.transfer_number:
            textos += [campaign.transfer_script, FRASE_TRANSFERENCIA_FALLIDA]
        variantes: list[str] = []
        for t in textos:
            if t and "{nombre}" in t:
                variantes += [con_nombre(t, None)] + [con_nombre(t, n) for n in nombres]
            else:
                variantes.append(t)
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

    return {"campaigns": len(campaigns), "texts": unique, "grupos": grupos}


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
