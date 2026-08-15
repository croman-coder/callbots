"""Panel de administración: campañas, preguntas, destinatarios y resultados.

Renderizado en el servidor con Jinja2 y formularios HTML planos. Sin build step
ni dependencias por CDN: el servidor puede no tener salida a internet.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.db import get_db
from app.deps import require_admin
from app.routers.simulator import emitir_ticket
from app.bitrix.client import normalize_phone_py
from app.scheduling import next_call_slot, parse_days, parse_hhmm
from app.services import voicebox
from app.services.scoring import SATISFACTORY_MIN
from app.models import (
    Answer,
    CallAnalysis,
    CallAttempt,
    Campaign,
    Question,
    QuestionType,
    SurveyTarget,
    TargetStatus,
)

log = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

router = APIRouter(tags=["panel"], dependencies=[Depends(require_admin)])

# El identificador del enum dice "scale_1_10" pero el rango real es 0-10, así que
# el panel muestra la etiqueta y no el valor crudo.
QUESTION_TYPE_LABELS = {
    QuestionType.SCALE_1_10: "escala 0-10",
    QuestionType.SCALE_1_5: "escala 1-5 (heredada)",
    QuestionType.YES_NO: "sí / no",
    QuestionType.NUMERIC: "número",
    QuestionType.OPEN: "respuesta libre",
}


def _redirect(path: str) -> RedirectResponse:
    """303 para que el navegador convierta el POST en GET y no reenvíe el form."""
    return RedirectResponse(path, status_code=303)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    counts = dict(
        db.execute(
            select(SurveyTarget.status, func.count(SurveyTarget.id)).group_by(
                SurveyTarget.status
            )
        ).all()
    )

    today_start = datetime.now(settings.timezone).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    calls_today = db.scalar(
        select(func.count(CallAttempt.id)).where(CallAttempt.started_at >= today_start)
    ) or 0

    avg_score = db.scalar(select(func.avg(CallAnalysis.satisfaction_score)))

    completed = counts.get(TargetStatus.COMPLETED, 0)
    contacted = completed + counts.get(TargetStatus.NO_ANSWER, 0)
    response_rate = (completed / contacted * 100) if contacted else 0.0

    # Distribución de sentimiento
    sentiment = dict(
        db.execute(
            select(CallAnalysis.sentiment, func.count(CallAnalysis.id)).group_by(
                CallAnalysis.sentiment
            )
        ).all()
    )

    # Promedio por pregunta: dónde se cae el puntaje
    per_question = db.execute(
        select(
            Question.text,
            Question.qtype,
            func.avg(Answer.value_numeric),
            func.count(Answer.id),
        )
        .join(Answer, Answer.question_id == Question.id)
        .where(Answer.value_numeric.isnot(None))
        .group_by(Question.id, Question.text, Question.qtype)
        .order_by(func.avg(Answer.value_numeric))
    ).all()

    recent_calls = db.scalars(
        select(CallAttempt)
        .options(
            selectinload(CallAttempt.target),
            selectinload(CallAttempt.analysis),
        )
        .order_by(CallAttempt.started_at.desc())
        .limit(15)
    ).all()

    needs_followup = db.scalars(
        select(CallAnalysis)
        .options(selectinload(CallAnalysis.call).selectinload(CallAttempt.target))
        .where(CallAnalysis.requires_followup.is_(True))
        .order_by(CallAnalysis.created_at.desc())
        .limit(10)
    ).all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "counts": counts,
            "total": sum(counts.values()),
            "calls_today": calls_today,
            # `if avg_score` convertía un promedio real de 0.0 en "sin datos":
            # justo el caso que más urge mirar.
            "avg_score": round(avg_score, 1) if avg_score is not None else None,
            "response_rate": round(response_rate, 1),
            # El porcentaje solo se entiende con su denominador: 100% sobre un
            # contactado no dice lo mismo que 100% sobre ochenta.
            "contacted": contacted,
            "sentiment": sentiment,
            "per_question": per_question,
            "recent_calls": recent_calls,
            "needs_followup": needs_followup,
            "TargetStatus": TargetStatus,
            "satisfactory_min": SATISFACTORY_MIN,
        },
    )


# ---------------------------------------------------------------------------
# Campañas
# ---------------------------------------------------------------------------
@router.get("/campaigns", response_class=HTMLResponse)
def list_campaigns(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    campaigns = db.scalars(
        select(Campaign).options(selectinload(Campaign.questions)).order_by(Campaign.id)
    ).all()

    stats = {
        campaign_id: {"targets": targets, "completed": completed}
        for campaign_id, targets, completed in db.execute(
            select(
                SurveyTarget.campaign_id,
                func.count(distinct(SurveyTarget.id)),
                func.count(distinct(SurveyTarget.id)).filter(
                    SurveyTarget.status == TargetStatus.COMPLETED
                ),
            ).group_by(SurveyTarget.campaign_id)
        ).all()
    }

    return templates.TemplateResponse(
        request,
        "campaigns.html",
        {"campaigns": campaigns, "stats": stats, "default_entity": settings.bitrix_entity_type_id},
    )


@router.post("/campaigns")
def create_campaign(
    name: str = Form(...),
    bitrix_entity_type_id: int = Form(...),
    trigger_field: str = Form(...),
    delay_hours: int = Form(48),
    description: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    campaign = Campaign(
        name=name.strip(),
        description=description.strip() or None,
        bitrix_entity_type_id=bitrix_entity_type_id,
        trigger_field=trigger_field.strip(),
        delay_hours=delay_hours,
        call_window_start=settings.call_window_start.strftime("%H:%M"),
        call_window_end=settings.call_window_end.strftime("%H:%M"),
        call_window_days=settings.call_window_days,
        max_attempts=settings.max_call_attempts,
        retry_interval_minutes=settings.retry_interval_minutes,
        is_active=False,  # se activa a mano recién cuando tiene preguntas
    )
    db.add(campaign)
    db.commit()
    log.info("Campaña creada: %s (id=%s)", campaign.name, campaign.id)
    return _redirect(f"/campaigns/{campaign.id}")


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail(
    campaign_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")

    target_counts = dict(
        db.execute(
            select(SurveyTarget.status, func.count(SurveyTarget.id))
            .where(SurveyTarget.campaign_id == campaign_id)
            .group_by(SurveyTarget.status)
        ).all()
    )

    return templates.TemplateResponse(
        request,
        "campaign_detail.html",
        {
            "campaign": campaign,
            "question_types": list(QuestionType),
            "type_labels": QUESTION_TYPE_LABELS,
            "target_counts": target_counts,
            "total_targets": sum(target_counts.values()),
        },
    )


@router.post("/campaigns/{campaign_id}")
def update_campaign(
    campaign_id: int,
    name: str = Form(...),
    description: str = Form(""),
    bitrix_entity_type_id: int = Form(...),
    trigger_field: str = Form(...),
    delay_hours: int = Form(48),
    call_window_start: str = Form("09:00"),
    call_window_end: str = Form("19:00"),
    call_window_days: str = Form("0,1,2,3,4,5"),
    max_attempts: int = Form(3),
    retry_interval_minutes: int = Form(180),
    intro_script: str = Form(...),
    outro_script: str = Form(...),
    fallback_script: str = Form(...),
    optout_script: str = Form(...),
    conversation_prompt: str = Form(""),
    analysis_prompt: str = Form(""),
    voice_speed: float = Form(1.0),
    voice_pitch: float = Form(1.0),
    voice_expressiveness: float = Form(0.667),
    voice_volume: float = Form(1.0),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")

    active_questions = [q for q in campaign.questions if q.is_active]
    if is_active and not active_questions:
        raise HTTPException(400, "No se puede activar una campaña sin preguntas activas")

    campaign.name = name.strip()
    campaign.description = description.strip() or None
    campaign.bitrix_entity_type_id = bitrix_entity_type_id
    campaign.trigger_field = trigger_field.strip()
    campaign.delay_hours = delay_hours
    campaign.call_window_start = call_window_start
    campaign.call_window_end = call_window_end
    campaign.call_window_days = call_window_days
    campaign.max_attempts = max_attempts
    campaign.retry_interval_minutes = retry_interval_minutes
    campaign.intro_script = intro_script
    campaign.outro_script = outro_script
    campaign.fallback_script = fallback_script
    campaign.optout_script = optout_script
    # Se acotan acá y no solo en el HTML: el form se puede saltear, y un
    # length_scale absurdo deja la voz irreconocible o tarda una eternidad.
    campaign.conversation_prompt = conversation_prompt.strip() or None
    campaign.analysis_prompt = analysis_prompt.strip() or None
    campaign.voice_speed = min(max(voice_speed, 0.5), 2.0)
    campaign.voice_pitch = min(max(voice_pitch, 0.7), 1.4)
    campaign.voice_expressiveness = min(max(voice_expressiveness, 0.0), 1.5)
    campaign.voice_volume = min(max(voice_volume, 0.2), 1.5)
    campaign.is_active = is_active

    db.commit()
    return _redirect(f"/campaigns/{campaign_id}")


# ---------------------------------------------------------------------------
# Preguntas
# ---------------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/questions")
def add_question(
    campaign_id: int,
    text: str = Form(...),
    qtype: QuestionType = Form(QuestionType.SCALE_1_10),
    max_answer_seconds: int = Form(20),
    max_retries: int = Form(1),
    counts_for_score: bool = Form(False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")

    next_position = (
        max((q.position for q in campaign.questions), default=0) + 1
    )

    db.add(
        Question(
            campaign_id=campaign_id,
            position=next_position,
            text=text.strip(),
            qtype=qtype,
            max_answer_seconds=max_answer_seconds,
            max_retries=max_retries,
            counts_for_score=counts_for_score,
        )
    )
    db.commit()
    return _redirect(f"/campaigns/{campaign_id}")


@router.post("/questions/{question_id}/delete")
def delete_question(question_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Pregunta inexistente")

    campaign_id = question.campaign_id
    db.delete(question)
    db.flush()

    # Recompactamos posiciones para que no queden huecos
    remaining = db.scalars(
        select(Question)
        .where(Question.campaign_id == campaign_id)
        .order_by(Question.position)
    ).all()
    for index, q in enumerate(remaining, start=1):
        q.position = index

    db.commit()
    return _redirect(f"/campaigns/{campaign_id}")


@router.post("/questions/{question_id}/move")
def move_question(
    question_id: int, direction: str = Form(...), db: Session = Depends(get_db)
) -> RedirectResponse:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(404, "Pregunta inexistente")

    siblings = db.scalars(
        select(Question)
        .where(Question.campaign_id == question.campaign_id)
        .order_by(Question.position)
    ).all()

    index = next(i for i, q in enumerate(siblings) if q.id == question_id)
    swap_with = index - 1 if direction == "up" else index + 1

    if not 0 <= swap_with < len(siblings):
        return _redirect(f"/campaigns/{question.campaign_id}")

    siblings[index], siblings[swap_with] = siblings[swap_with], siblings[index]

    # (campaign_id, position) es única: pasamos por posiciones negativas para no
    # chocar con la constraint a mitad de la reasignación.
    for i, q in enumerate(siblings, start=1):
        q.position = -i
    db.flush()
    for i, q in enumerate(siblings, start=1):
        q.position = i
    db.commit()

    return _redirect(f"/campaigns/{question.campaign_id}")


# ---------------------------------------------------------------------------
# Destinatarios
# ---------------------------------------------------------------------------
@router.get("/targets", response_class=HTMLResponse)
def list_targets(
    request: Request,
    status: str | None = None,
    # Llegan como texto a propósito. El <select> de "todas las campañas" manda
    # `campaign_id=` vacío, y declarado como int eso no es "sin filtro" sino un
    # 422: la página entera moría con un JSON de error apenas se tocaba el
    # filtro de estado. Mismo motivo para `page`, que viaja en los enlaces de
    # paginación junto al filtro vacío.
    campaign_id: str | None = None,
    page: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    def _entero(valor: str | None, por_defecto: int | None = None) -> int | None:
        try:
            return int(valor) if valor not in (None, "") else por_defecto
        except ValueError:
            return por_defecto

    campaign_id = _entero(campaign_id)
    page = max(1, _entero(page, 1) or 1)
    per_page = 50

    # Los filtros se arman una vez y se aplican a las dos consultas. Contar sobre
    # una query con selectinload() no es válido: el eager-load no sobrevive al
    # subquery.
    filters = []
    if status:
        filters.append(SurveyTarget.status == TargetStatus(status))
    if campaign_id:
        filters.append(SurveyTarget.campaign_id == campaign_id)

    total = db.scalar(
        select(func.count(SurveyTarget.id)).where(*filters)
    ) or 0

    targets = db.scalars(
        select(SurveyTarget)
        .options(selectinload(SurveyTarget.campaign))
        .where(*filters)
        .order_by(SurveyTarget.scheduled_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).all()

    campaigns = db.scalars(select(Campaign).order_by(Campaign.name)).all()

    return templates.TemplateResponse(
        request,
        "targets.html",
        {
            "targets": targets,
            "campaigns": campaigns,
            "statuses": list(TargetStatus),
            "current_status": status,
            "current_campaign": campaign_id,
            "page": page,
            "total": total,
            "pages": max(1, (total + per_page - 1) // per_page),
        },
    )


@router.post("/targets")
def add_targets(
    campaign_id: int = Form(...),
    lote: str = Form(...),
    cuando: str = Form("ahora"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Carga destinatarios a mano, sin pasar por Bitrix.

    Una línea por persona: `teléfono, nombre`. El nombre es opcional.
    Los teléfonos se normalizan a E.164 paraguayo, igual que los que vienen
    del sync, así que `0981 123 456` y `+595981123456` son el mismo número.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")

    tz = ZoneInfo(settings.tz)
    ahora = datetime.now(timezone.utc)
    # "ahora" agenda en el próximo hueco llamable; "espera" respeta la demora
    # de la campaña, como si el ingreso al taller fuera este momento.
    trigger_at = ahora
    base = ahora if cuando == "ahora" else ahora + timedelta(hours=campaign.delay_hours)

    # parse_hhmm pide un fallback: si la campaña tiene el horario mal escrito,
    # se usa el del entorno en vez de reventar.
    scheduled_at = next_call_slot(
        base,
        parse_hhmm(campaign.call_window_start, settings.call_window_start),
        parse_hhmm(campaign.call_window_end, settings.call_window_end),
        parse_days(campaign.call_window_days),
        tz,
    )

    creados = duplicados = invalidos = 0
    # Los del propio lote todavía no están en la base cuando se consulta, así
    # que un teléfono repetido dentro del mismo pegado se colaba entero.
    en_este_lote: set[str] = set()

    for linea in lote.splitlines():
        linea = linea.strip()
        if not linea:
            continue

        partes = [p.strip() for p in linea.replace(";", ",").split(",")]
        telefono = normalize_phone_py(partes[0])
        nombre = partes[1] if len(partes) > 1 and partes[1] else None

        if not telefono:
            invalidos += 1
            continue

        ya_esta = db.scalar(
            select(SurveyTarget).where(
                SurveyTarget.campaign_id == campaign_id,
                SurveyTarget.phone == telefono,
                SurveyTarget.status.in_(
                    [
                        TargetStatus.PENDING,
                        TargetStatus.SCHEDULED,
                        TargetStatus.QUEUED,
                        TargetStatus.CALLING,
                    ]
                ),
            )
        )
        if ya_esta or telefono in en_este_lote:
            duplicados += 1
            continue

        en_este_lote.add(telefono)

        db.add(
            SurveyTarget(
                campaign_id=campaign_id,
                bitrix_entity_type_id=None,
                bitrix_entity_id=None,
                contact_name=nombre,
                phone=telefono,
                trigger_at=trigger_at,
                scheduled_at=scheduled_at,
                status=TargetStatus.SCHEDULED,
            )
        )
        creados += 1

    db.commit()
    log.info(
        "Alta manual en campaña %s: %d creados, %d duplicados, %d inválidos",
        campaign_id, creados, duplicados, invalidos,
    )
    return _redirect(
        f"/targets?campaign_id={campaign_id}&ok={creados}"
        f"&dup={duplicados}&mal={invalidos}"
    )


@router.post("/targets/{target_id}/call-now")
def trigger_call(target_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    target = db.get(SurveyTarget, target_id)
    if target is None:
        raise HTTPException(404, "Destinatario inexistente")

    from app.scheduler.tasks import call_now

    call_now.delay(target_id)
    log.info("Llamada manual disparada para target %s", target_id)
    return _redirect("/targets")


@router.post("/targets/{target_id}/reschedule")
def reschedule_target(target_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """Vuelve a poner un destinatario en cola, reseteando los intentos."""
    target = db.get(SurveyTarget, target_id)
    if target is None:
        raise HTTPException(404, "Destinatario inexistente")

    target.status = TargetStatus.SCHEDULED
    target.attempts = 0
    target.last_error = None
    target.scheduled_at = datetime.now(timezone.utc)
    db.commit()
    return _redirect("/targets")


# ---------------------------------------------------------------------------
# Llamadas
# ---------------------------------------------------------------------------
@router.get("/calls/{call_id}", response_class=HTMLResponse)
def call_detail(
    call_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    call = db.get(CallAttempt, call_id)
    if call is None:
        raise HTTPException(404, "Llamada inexistente")

    return templates.TemplateResponse(
        request,
        "call_detail.html",
        {
            "call": call,
            "QuestionType": QuestionType,
            "satisfactory_min": SATISFACTORY_MIN,
        },
    )


@router.post("/calls/{call_id}/reanalyze")
def reanalyze(call_id: int) -> RedirectResponse:
    from app.scheduler.tasks import reanalyze_call

    reanalyze_call.delay(call_id)
    return _redirect(f"/calls/{call_id}")


# ---------------------------------------------------------------------------
# Voz del bot
# ---------------------------------------------------------------------------
@router.get("/simulador", response_class=HTMLResponse)
def simulador_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Página para hablar con el bot desde el navegador.

    Emite un ticket de un solo uso porque el navegador no manda las
    credenciales de HTTP Basic al abrir un WebSocket.
    """
    campaign = db.scalar(
        select(Campaign).where(Campaign.is_active.is_(True)).order_by(Campaign.id)
    )
    preguntas = len([q for q in campaign.questions if q.is_active]) if campaign else 0

    return templates.TemplateResponse(
        request,
        "simulator.html",
        {
            "campaign": campaign,
            "preguntas": preguntas,
            "ticket": emitir_ticket(),
        },
    )


@router.get("/voices", response_class=HTMLResponse)
def voices_page(
    request: Request,
    error: str | None = None,
    ok: str | None = None,
    preview: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    profiles: list[dict] = []
    load_error: str | None = None

    if voicebox.is_configured():
        try:
            profiles = voicebox.list_profiles()
        except voicebox.VoiceboxError as exc:
            load_error = str(exc)

    active = next(
        (p for p in profiles if p.get("id") == settings.voicebox_profile_id), None
    )

    campaigns = db.scalars(select(Campaign).order_by(Campaign.name)).all()

    return templates.TemplateResponse(
        request,
        "voices.html",
        {
            "campaigns": campaigns,
            "configured": voicebox.is_configured(),
            "profiles": profiles,
            "active": active,
            "active_id": settings.voicebox_profile_id,
            "load_error": load_error,
            "error": error,
            "ok": ok,
            "preview": preview,
            "settings": settings,
        },
    )


@router.post("/voices/campaign/{campaign_id}")
def update_campaign_voice(
    campaign_id: int,
    voice_speed: float = Form(1.0),
    voice_pitch: float = Form(1.0),
    voice_expressiveness: float = Form(0.667),
    voice_volume: float = Form(1.0),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Guarda solo la modulación, desde la página de Voz.

    Los mismos controles están en el detalle de la campaña, pero ahí quedan
    abajo del guion y nadie los encuentra: el lugar donde uno busca cómo suena
    el bot es la pestaña que se llama Voz.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")

    campaign.voice_speed = min(max(voice_speed, 0.5), 2.0)
    campaign.voice_pitch = min(max(voice_pitch, 0.7), 1.4)
    campaign.voice_expressiveness = min(max(voice_expressiveness, 0.0), 1.5)
    campaign.voice_volume = min(max(voice_volume, 0.2), 1.5)
    db.commit()

    return _redirect(f"/voices?ok=Voz+actualizada+en+{quote_plus(campaign.name)}")


@router.post("/voices/clone")
async def clone_voice(
    name: str = Form(...),
    reference_text: str = Form(...),
    audio: UploadFile = File(...),
) -> RedirectResponse:
    """Clona una voz desde una grabación subida por el navegador."""
    content = await audio.read()

    if not content:
        return _redirect(f"/voices?error={quote_plus('El archivo de audio está vacío')}")

    # 25 MB alcanza de sobra para 30 segundos en cualquier formato razonable;
    # más que eso es un archivo equivocado.
    if len(content) > 25 * 1024 * 1024:
        return _redirect(f"/voices?error={quote_plus('El audio supera los 25 MB')}")

    try:
        # voicebox usa httpx sincrónico y la clonación tarda minutos: si corriera
        # en el event loop, la API entera quedaría congelada mientras procesa.
        profile = await asyncio.to_thread(
            voicebox.clone_voice,
            name.strip(),
            content,
            audio.filename or "muestra.wav",
            reference_text.strip(),
        )
    except voicebox.VoiceboxError as exc:
        return _redirect(f"/voices?error={quote_plus(str(exc)[:300])}")

    return _redirect(
        f"/voices?ok={quote_plus('Voz clonada: ' + profile.get('name', ''))}"
        f"&preview={profile['id']}"
    )


@router.get("/voices/preview")
def preview_voice(profile_id: str, text: str | None = None) -> Response:
    """Genera y devuelve un WAV para escuchar en el navegador."""
    sample = text or (
        "Hola, buenos días. Le hablamos del servicio de posventa del taller. "
        "Estamos haciendo una encuesta muy breve sobre su última visita."
    )

    try:
        wav = voicebox.generate(profile_id, sample)
    except voicebox.VoiceboxError as exc:
        raise HTTPException(502, str(exc))

    return Response(
        content=wav,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


# ---------------------------------------------------------------------------
# Acciones globales
# ---------------------------------------------------------------------------
@router.post("/sync-now")
def sync_now() -> RedirectResponse:
    from app.scheduler.tasks import sync_bitrix

    sync_bitrix.delay()
    log.info("Sincronización con Bitrix disparada a mano")
    return _redirect("/")


@router.get("/health-detail", response_class=HTMLResponse)
def health_detail(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Diagnóstico: verifica Bitrix, Asterisk y Ollama de una sola pasada."""
    checks: list[dict] = []

    # Postgres
    try:
        db.execute(select(func.now()))
        checks.append({"name": "Postgres", "ok": True, "detail": "conectado"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "Postgres", "ok": False, "detail": str(exc)[:200]})

    # Bitrix
    if not settings.bitrix_webhook_url.startswith("http"):
        # Desactivado a propósito, no roto: el callbot funciona igual y los
        # resultados quedan en el panel. Marcarlo en rojo entrena a ignorar
        # el diagnóstico.
        checks.append(
            {
                "name": "Bitrix24",
                "ok": True,
                "detail": (
                    "no configurado; los destinatarios se cargan a mano y el "
                    "resultado queda solo en el panel"
                ),
            }
        )
    else:
        try:
            from app.bitrix.client import BitrixClient

            with BitrixClient() as client:
                profile = client.call("profile")
            nombre = f"{profile.get('NAME', '?')} {profile.get('LAST_NAME', '')}".strip()
            checks.append(
                {"name": "Bitrix24", "ok": True, "detail": f"portal de {nombre}"}
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {"name": "Bitrix24", "ok": False, "detail": str(exc)[:200]}
            )

    # Asterisk
    try:
        from app.services.asterisk_ari import AriClient

        with AriClient() as ari:
            info = ari.ping()
        checks.append(
            {
                "name": "Asterisk (ARI)",
                "ok": True,
                "detail": f"versión {info.get('system', {}).get('version', '?')}",
            }
        )
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "Asterisk (ARI)", "ok": False, "detail": str(exc)[:200]})

    # Voicebox (voz clonada). Si no está configurado no es una falla: el
    # callbot funciona con Piper, solo que con voz genérica.
    if not voicebox.is_configured():
        checks.append(
            {
                "name": "Voicebox (voz clonada)",
                "ok": True,
                "detail": "no configurado; el bot habla con Piper (voz genérica)",
            }
        )
    else:
        try:
            profiles = voicebox.list_profiles()
            match = next(
                (p for p in profiles if p.get("id") == settings.voicebox_profile_id),
                None,
            )
            checks.append(
                {
                    "name": "Voicebox (voz clonada)",
                    "ok": match is not None,
                    "detail": (
                        f"voz «{match.get('name')}» activa"
                        if match
                        else (
                            f"VOICEBOX_PROFILE_ID={settings.voicebox_profile_id or '(vacío)'} "
                            "no coincide con ningún perfil. Disponibles: "
                            + (", ".join(p.get("name", "?") for p in profiles) or "ninguno")
                        )
                    ),
                }
            )
        except voicebox.VoiceboxError as exc:
            checks.append(
                {
                    "name": "Voicebox (voz clonada)",
                    "ok": False,
                    "detail": f"{str(exc)[:160]} — el bot va a hablar con Piper",
                }
            )

    # Ollama
    if settings.ollama_enabled:
        try:
            import httpx

            response = httpx.get(f"{settings.ollama_url.rstrip('/')}/api/tags", timeout=10)
            models = [m["name"] for m in response.json().get("models", [])]
            has_model = any(settings.ollama_model.split(":")[0] in m for m in models)
            checks.append(
                {
                    "name": "Ollama",
                    "ok": has_model,
                    "detail": (
                        f"modelo {settings.ollama_model} disponible"
                        if has_model
                        else f"falta {settings.ollama_model}. Modelos: {', '.join(models) or 'ninguno'}"
                    ),
                }
            )
        except Exception as exc:  # noqa: BLE001
            checks.append({"name": "Ollama", "ok": False, "detail": str(exc)[:200]})

    return templates.TemplateResponse(
        request, "health.html", {"checks": checks, "settings": settings}
    )
