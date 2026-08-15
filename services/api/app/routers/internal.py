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
import uuid as uuid_mod
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
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

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_internal_token)],
)

# UUID que manda el dialplan en la extensión 9000 (prueba manual desde softphone).
# No persiste nada: sirve para escuchar el guion sin ensuciar los datos.
DEMO_UUID = uuid_mod.UUID("00000000-0000-0000-0000-000000000000")


def _load_call(db: Session, session_uuid: uuid_mod.UUID) -> CallAttempt:
    call = db.scalar(
        select(CallAttempt).where(CallAttempt.session_uuid == session_uuid)
    )
    if call is None:
        raise HTTPException(404, f"No hay llamada para la sesión {session_uuid}")
    return call


def _active_questions(campaign: Campaign) -> list[Question]:
    return [q for q in campaign.questions if q.is_active]


# ---------------------------------------------------------------------------
@router.get("/sessions/{session_uuid}/script", response_model=SurveyScript)
def get_script(session_uuid: uuid_mod.UUID, db: Session = Depends(get_db)) -> SurveyScript:
    """Devuelve el guion completo. Es lo primero que pide el voice-agent."""

    # --- modo demo (extensión 9000) ---
    if session_uuid == DEMO_UUID:
        campaign = db.scalar(
            select(Campaign)
            .where(Campaign.is_active.is_(True))
            .order_by(Campaign.id)
        )
        if campaign is None:
            raise HTTPException(404, "No hay ninguna campaña activa para demostrar")

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
    if session_uuid == DEMO_UUID:
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

    if session_uuid == DEMO_UUID:
        return AnswerResult(
            understood=result.understood,
            value=result.value,
            is_optout=result.is_optout,
            is_silence=result.is_silence,
            should_retry=should_retry,
            saved=False,
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
    )


# ---------------------------------------------------------------------------
@router.post("/sessions/{session_uuid}/finished")
def session_finished(
    session_uuid: uuid_mod.UUID,
    payload: SessionFinished,
    db: Session = Depends(get_db),
) -> dict:
    """Cierra la llamada y encola el análisis."""
    if session_uuid == DEMO_UUID:
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
@router.post("/sessions/{session_uuid}/reply")
async def conversational_reply(
    session_uuid: uuid_mod.UUID,
    body: ReplyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Qué contestarle al cliente cuando dijo algo que no es una respuesta.

    Solo se llama en la rama de excepción: si el cliente contestó bien, el
    voice-agent sigue de largo sin pasar por acá y sin pagar la latencia.

    Devuelve `{"reply": null}` cuando Gemini no está configurado, tarda de más
    o falla. El voice-agent usa entonces la frase fija de la campaña.
    """
    if session_uuid == DEMO_UUID:
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
        .where(Campaign.is_active.is_(True))
    ).all()

    # Se agrupa por campaña porque cada una tiene su propia modulación de voz:
    # la misma frase con otra velocidad o tono es otro audio, y cachearla con
    # los parámetros equivocados haría que el precalentado no sirva de nada.
    grupos = []
    todos: list[str] = []

    for campaign in campaigns:
        textos = [
            campaign.intro_script,
            campaign.outro_script,
            campaign.fallback_script,
            campaign.optout_script,
        ]
        textos += [q.text for q in campaign.questions if q.is_active]
        limpios = [t.strip() for t in dict.fromkeys(textos) if t and t.strip()]
        todos += limpios

        grupos.append(
            {
                "voice": {
                    "speed": campaign.voice_speed,
                    "pitch": campaign.voice_pitch,
                    "expressiveness": campaign.voice_expressiveness,
                    "volume": campaign.voice_volume,
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
    if session_uuid == DEMO_UUID:
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
