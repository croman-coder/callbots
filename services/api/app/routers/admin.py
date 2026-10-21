"""Panel de administración: campañas, preguntas, destinatarios y resultados.

Renderizado en el servidor con Jinja2 y formularios HTML planos. Sin build step
ni dependencias por CDN: el servidor puede no tener salida a internet.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import quote_plus, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app import agentes, pronunciaciones, versiones, voces
from app.config import settings
from app.etiquetas import etiqueta
from app.plantillas import templates
from app.db import get_db
from app.deps import require_admin
from app.routers.simulator import emitir_ticket
from app.bitrix.client import normalize_phone_py
from app.scheduling import next_call_slot, parse_days, parse_hhmm, texto_dias
from app.services import calidad, contactabilidad, elevenlabs, importar, preguntar, voicebox, voicestudio
from app.services.export import construir_excel
from app.services.scoring import SATISFACTORY_MIN
from app.models import (
    Answer,
    CallAnalysis,
    CallAttempt,
    CallOutcome,
    Campaign,
    Question,
    QuestionType,
    SurveyTarget,
    TargetStatus,
)

log = logging.getLogger(__name__)


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


def _entero(valor: str | None, por_defecto: int | None = None) -> int | None:
    """Convierte a int el valor de un <select> o de la paginación, tolerando el vacío.

    Un `<option value="">` manda `campo=` en la query. Si el parámetro se
    declarara `int | None`, FastAPI rechazaría esa cadena vacía con un 422 y
    elegir "todas" rompería la página entera con un JSON de error. Los
    formularios HTML siempre mandan el campo, así que la tolerancia vive acá y
    no repetida en cada ruta.
    """
    try:
        return int(valor) if valor not in (None, "") else por_defecto
    except ValueError:
        return por_defecto


def _query_sin(params, *excluir: str) -> str:
    """Rearma la query string omitiendo claves vacías y las excluidas."""
    return urlencode(
        {k: v for k, v in params.items() if v not in ("", None) and k not in excluir}
    )


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
        .where(CallAnalysis.requires_followup.is_(True), CallAnalysis.followup_done_at.is_(None))
        .order_by(CallAnalysis.created_at.desc())
        .limit(10)
    ).all()
    pendientes_total = db.scalar(
        select(func.count(CallAnalysis.id)).where(
            CallAnalysis.requires_followup.is_(True), CallAnalysis.followup_done_at.is_(None)
        )
    ) or 0
    atendidos_total = db.scalar(
        select(func.count(CallAnalysis.id)).where(CallAnalysis.followup_done_at.is_not(None))
    ) or 0

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
            "pendientes_total": pendientes_total,
            "atendidos_total": atendidos_total,
            "TargetStatus": TargetStatus,
            "satisfactory_min": SATISFACTORY_MIN,
        },
    )


# ---------------------------------------------------------------------------
# Campañas
# ---------------------------------------------------------------------------
PERIODOS = {"7": 7, "30": 30, "90": 90, "todo": None}


def _metricas_agentes(db: Session, desde: datetime | None) -> dict[int, dict]:
    """Por agente (campaña): contactos, contactados, llamadas, metas logradas y calidad media.

    «Meta lograda» = encuesta completada. Los contactos cuentan los destinatarios cargados en el
    período; lo demás, las llamadas hechas en el período.
    """
    con_destinatarios = select(SurveyTarget.campaign_id, func.count(SurveyTarget.id))
    if desde:
        con_destinatarios = con_destinatarios.where(SurveyTarget.created_at >= desde)
    metricas: dict[int, dict] = {
        campaign_id: {"contactos": n, "contactados": 0, "llamadas": 0, "metas": 0, "calidad": None}
        for campaign_id, n in db.execute(con_destinatarios.group_by(SurveyTarget.campaign_id)).all()
    }

    en_periodo = [CallAttempt.started_at >= desde] if desde else []
    for campaign_id, llamadas, contactados, metas in db.execute(
        select(
            SurveyTarget.campaign_id,
            func.count(CallAttempt.id),
            func.count(distinct(SurveyTarget.id)).filter(CallAttempt.answered_at.is_not(None)),
            func.count(distinct(SurveyTarget.id)).filter(CallAttempt.outcome == CallOutcome.COMPLETED),
        )
        .join(SurveyTarget, SurveyTarget.id == CallAttempt.target_id)
        .where(*en_periodo)
        .group_by(SurveyTarget.campaign_id)
    ).all():
        m = metricas.setdefault(
            campaign_id, {"contactos": 0, "contactados": 0, "llamadas": 0, "metas": 0, "calidad": None}
        )
        m.update(llamadas=llamadas, contactados=contactados, metas=metas)

    # La calidad se calcula llamada por llamada (ver services/calidad.py): se promedia lo atendido.
    atendidas = db.scalars(
        select(CallAttempt)
        .join(SurveyTarget, SurveyTarget.id == CallAttempt.target_id)
        .where(*en_periodo, CallAttempt.answered_at.is_not(None))
        .options(selectinload(CallAttempt.answers), selectinload(CallAttempt.target))
        .order_by(CallAttempt.started_at.desc())
        .limit(5000)
    ).all()
    puntajes: dict[int, list[int]] = {}
    for c in atendidas:
        q = calidad.calidad_de(c)
        if q.puntaje is not None:
            puntajes.setdefault(c.target.campaign_id, []).append(q.puntaje)
    for campaign_id, lista in puntajes.items():
        if campaign_id in metricas:
            metricas[campaign_id]["calidad"] = round(sum(lista) / len(lista))
    return metricas


@router.get("/campaigns", response_class=HTMLResponse)
def list_campaigns(request: Request, dias: str = "30", db: Session = Depends(get_db)) -> HTMLResponse:
    dias = dias if dias in PERIODOS else "30"
    desde = datetime.now(timezone.utc) - timedelta(days=PERIODOS[dias]) if PERIODOS[dias] else None
    campaigns = db.scalars(
        select(Campaign).options(selectinload(Campaign.questions)).order_by(Campaign.id)
    ).all()

    return templates.TemplateResponse(
        request,
        "campaigns.html",
        {
            "campaigns": campaigns,
            "metricas": _metricas_agentes(db, desde),
            "dias": dias,
            "periodos": {"7": "Últimos 7 días", "30": "Últimos 30 días", "90": "Últimos 90 días", "todo": "Todo"},
            "plantillas": list(agentes.PLANTILLAS.values()),
            "propositos": agentes.PROPOSITOS,
            "empresa_por_defecto": "Santa Rosa Paraguay",
        },
    )


@router.post("/campaigns")
def create_campaign(
    plantilla: str = Form("personalizado"),
    name: str = Form(...),
    proposito: str = Form(""),
    instrucciones: str = Form(""),
    empresa: str = Form(""),
    empresa_descripcion: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    base = agentes.PLANTILLAS.get(plantilla)
    if base is None or not base.disponible:
        raise HTTPException(400, "Plantilla inexistente")
    name = name.strip()
    if not name:
        raise HTTPException(400, "Falta el nombre del agente")
    proposito = proposito if proposito in agentes.PROPOSITOS else base.proposito

    campaign = Campaign(
        name=name[:160],
        description=proposito,
        # Una campaña cargada por Excel no usa el disparador de Bitrix, pero la columna lo exige:
        # queda el de posventa, que se cambia desde la ficha del agente.
        bitrix_entity_type_id=settings.bitrix_entity_type_id,
        trigger_field=settings.bitrix_field_workshop_entry,
        delay_hours=48,
        call_window_start=settings.call_window_start.strftime("%H:%M"),
        call_window_end=settings.call_window_end.strftime("%H:%M"),
        call_window_days=settings.call_window_days,
        max_attempts=settings.max_call_attempts,
        retry_interval_minutes=settings.retry_interval_minutes,
        conversation_prompt=agentes.armar_contexto(empresa[:160], empresa_descripcion[:2000], instrucciones[:2000]),
        empresa_nombre=empresa.strip()[:160] or None,
        empresa_descripcion=empresa_descripcion.strip()[:4000] or None,
        instrucciones_importantes=instrucciones.strip()[:4000] or None,
        is_active=False,  # se activa a mano recién cuando tiene preguntas
    )
    if base.intro:
        campaign.intro_script = base.intro
    if base.outro:
        campaign.outro_script = base.outro
    for posicion, q in enumerate(base.preguntas, start=1):
        campaign.questions.append(
            Question(
                position=posicion,
                text=q.texto,
                qtype=q.tipo,
                counts_for_score=q.puntua,
                max_answer_seconds=q.segundos,
            )
        )
    db.add(campaign)
    db.commit()
    log.info("Agente creado: %s (id=%s, plantilla=%s)", campaign.name, campaign.id, plantilla)
    # El último paso del asistente («Probá tu agente») es la pestaña Probar de la ficha: ahí Claude
    # redacta las instrucciones y se habla con el agente desde el navegador.
    return _redirect(f"/campaigns/{campaign.id}?tab=probar&nuevo=1")


@router.post("/agentes/contexto-empresa")
async def contexto_empresa(request: Request, url: str = Form("")) -> JSONResponse:
    """«Generar contexto»: Claude lee el sitio web de la empresa y devuelve una descripción."""
    usuario = request.state.principal.usuario if getattr(request.state, "principal", None) else "?"
    if not agentes.permitir(usuario):
        return JSONResponse({"error": "Ya generaste varios contextos en la última hora. Probá más tarde."}, 429)
    try:
        return JSONResponse({"descripcion": await agentes.contexto_desde_sitio(url)})
    except agentes.SitioInvalido as exc:
        return JSONResponse({"error": str(exc)}, 400)


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
    return _redirect(f"/campaigns/{campaign_id}?tab=configuracion#preguntas")


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
    return _redirect(f"/campaigns/{campaign_id}?tab=configuracion#preguntas")


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
        return _redirect(f"/campaigns/{question.campaign_id}?tab=configuracion#preguntas")

    siblings[index], siblings[swap_with] = siblings[swap_with], siblings[index]

    # (campaign_id, position) es única: pasamos por posiciones negativas para no
    # chocar con la constraint a mitad de la reasignación.
    for i, q in enumerate(siblings, start=1):
        q.position = -i
    db.flush()
    for i, q in enumerate(siblings, start=1):
        q.position = i
    db.commit()

    return _redirect(f"/campaigns/{question.campaign_id}?tab=configuracion#preguntas")


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
    filas = []
    for linea in lote.splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = [p.strip() for p in linea.replace(";", ",").split(",")]
        filas.append((partes[0], partes[1] if len(partes) > 1 and partes[1] else None))
    return _alta_de_destinatarios(db, campaign_id, filas, cuando, "manual")


@router.post("/targets/archivo")
async def add_targets_archivo(
    campaign_id: int = Form(...),
    cuando: str = Form("ahora"),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Carga destinatarios desde un Excel (.xlsx) o un CSV, con o sin encabezados."""
    contenido = await archivo.read(5 * 1024 * 1024 + 1)
    if len(contenido) > 5 * 1024 * 1024:
        return _redirect(f"/targets?error={quote_plus('El archivo supera los 5 MB')}#cargar")
    try:
        filas = importar.leer_filas(contenido, archivo.filename or "")
    except importar.ArchivoInvalido as exc:
        return _redirect(f"/targets?error={quote_plus(str(exc))}#cargar")
    return _alta_de_destinatarios(db, campaign_id, filas, cuando, f"archivo {archivo.filename}")


def _alta_de_destinatarios(
    db: Session, campaign_id: int, filas: list[tuple[str, str | None]], cuando: str, origen: str
) -> RedirectResponse:
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

    for crudo, nombre in filas:
        telefono = normalize_phone_py(crudo)
        nombre = (nombre or "").strip()[:160] or None

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
        "Alta (%s) en campaña %s: %d creados, %d duplicados, %d inválidos",
        origen, campaign_id, creados, duplicados, invalidos,
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
    quien = target.contact_name or target.phone
    return _redirect(f"/targets?aviso={quote_plus(f'Llamando a {quien}. El resultado aparece en Historial cuando termine.')}")


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
    quien = target.contact_name or target.phone
    return _redirect(f"/targets?aviso={quote_plus(f'{quien} vuelve a la cola con los intentos en cero: se llama en el próximo horario permitido.')}")


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
            "settings_tz": settings.timezone,
            "QuestionType": QuestionType,
            "satisfactory_min": SATISFACTORY_MIN,
            "calidad": calidad.calidad_de(call),
            "con_audio": {a.id for a in call.answers if _audio_de(a) is not None},
        },
    )




def _audio_de(answer: Answer) -> Path | None:
    """La ruta del WAV de una respuesta, solo si existe y está dentro del directorio de grabaciones.

    La ruta viene de la base (la escribe el voice-agent): se valida igual, porque servir un
    archivo por una ruta guardada sin chequear es leer cualquier archivo del contenedor.
    """
    if not answer.audio_path:
        return None
    try:
        ruta = Path(answer.audio_path).resolve()
        base = Path(settings.recordings_dir).resolve()
    except (OSError, RuntimeError):
        return None
    if base not in ruta.parents or ruta.suffix.lower() != ".wav" or not ruta.is_file():
        return None
    return ruta


@router.get("/calls/{call_id}/answers/{answer_id}/audio")
def answer_audio(call_id: int, answer_id: int, db: Session = Depends(get_db)) -> Response:
    answer = db.get(Answer, answer_id)
    if answer is None or answer.call_id != call_id:
        raise HTTPException(404, "Respuesta inexistente")
    ruta = _audio_de(answer)
    if ruta is None:
        raise HTTPException(404, "Esta respuesta no tiene audio guardado")
    return FileResponse(ruta, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})


@router.post("/calls/{call_id}/followup")
def cerrar_seguimiento(
    call_id: int,
    request: Request,
    accion: str = Form("atendido"),
    nota: str = Form(""),
    volver: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Marca que alguien ya le devolvió la llamada al cliente (o lo reabre)."""
    analisis = db.scalar(select(CallAnalysis).where(CallAnalysis.call_id == call_id))
    if analisis is None:
        raise HTTPException(404, "Esa llamada no tiene análisis")

    if accion == "reabrir":
        analisis.followup_done_at = None
        analisis.followup_done_by = None
        analisis.followup_note = None
    else:
        principal = getattr(request.state, "principal", None)
        analisis.followup_done_at = datetime.now(timezone.utc)
        analisis.followup_done_by = (principal.usuario if principal else "")[:160] or None
        analisis.followup_note = nota.strip()[:1000] or None
    db.commit()

    # Solo se vuelve a una pantalla de este mismo sitio (nunca a una URL que venga del formulario).
    destino = f"/calls/{call_id}" if volver != "inicio" else "/"
    return _redirect(destino)


@router.post("/calls/{call_id}/reanalyze")
def reanalyze(call_id: int) -> RedirectResponse:
    from app.scheduler.tasks import reanalyze_call

    reanalyze_call.delay(call_id)
    return _redirect(f"/calls/{call_id}")


# ---------------------------------------------------------------------------
# Reportes
# ---------------------------------------------------------------------------
POR_PAGINA = 60


def _filtros_reporte(
    db: Session,
    desde: str | None,
    hasta: str | None,
    campaign_id: int | None,
    outcome: str | None,
    solo_alertas: bool,
) -> tuple[list, str]:
    """Traduce los filtros de la URL a condiciones SQL y a texto legible.

    La pantalla y el Excel comparten esta función a propósito: si cada uno
    armara sus filtros, el archivo descargado podría no coincidir con lo que la
    persona acaba de mirar, y no habría forma de notarlo.
    """
    condiciones = []
    partes: list[str] = []

    def _dia(valor: str) -> datetime | None:
        try:
            return datetime.fromisoformat(valor).replace(tzinfo=settings.timezone)
        except (ValueError, TypeError):
            return None

    if desde:
        inicio = _dia(desde)
        if inicio:
            condiciones.append(CallAttempt.started_at >= inicio)
            partes.append(f"desde {desde}")

    if hasta:
        fin = _dia(hasta)
        if fin:
            # El día "hasta" se incluye completo: quien pide 15/08 espera que
            # entren las llamadas de esa tarde, no solo las de medianoche.
            condiciones.append(CallAttempt.started_at < fin + timedelta(days=1))
            partes.append(f"hasta {hasta}")

    if campaign_id:
        condiciones.append(SurveyTarget.campaign_id == campaign_id)
        nombre = db.scalar(select(Campaign.name).where(Campaign.id == campaign_id))
        partes.append(f"campaña «{nombre or campaign_id}»")

    if outcome == "conectadas":
        # «Conectadas» de Dapta: el cliente atendió, terminara o no la encuesta.
        condiciones.append(CallAttempt.answered_at.is_not(None))
        partes.append("solo las que atendieron")
    elif outcome:
        try:
            condiciones.append(CallAttempt.outcome == CallOutcome(outcome))
            partes.append(f"resultado «{etiqueta(outcome)}»")
        except ValueError:
            pass

    if solo_alertas:
        condiciones.append(CallAnalysis.requires_followup.is_(True))
        partes.append("solo advertencias")

    return condiciones, " · ".join(partes)


def _con_joins(stmt, condiciones: list):
    """Aplica los joins y filtros del reporte a cualquier select.

    Definido una sola vez porque el listado, el conteo y los agregados tienen
    que recorrer exactamente las mismas filas. Con la cadena repetida en tres
    lugares, agregar un join después dejaría los totales desalineados con la
    tabla y nada avisaría.

    El outer join sobre CallAnalysis es deliberado: una llamada que no atendió
    no tiene análisis, y sigue siendo un resultado que hay que poder ver.
    """
    return (
        stmt.select_from(CallAttempt)
        .join(SurveyTarget, CallAttempt.target_id == SurveyTarget.id)
        .outerjoin(CallAnalysis, CallAnalysis.call_id == CallAttempt.id)
        .where(*condiciones)
    )


@router.get("/reportes", response_class=HTMLResponse)
def reportes(
    request: Request,
    desde: str | None = None,
    hasta: str | None = None,
    campaign_id: str | None = None,
    outcome: str | None = None,
    solo_alertas: bool = False,
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return _pagina_reportes(request, db, desde, hasta, campaign_id, outcome, solo_alertas, page)


@router.get("/historial", response_class=HTMLResponse)
def historial(
    request: Request,
    desde: str | None = None,
    hasta: str | None = None,
    campaign_id: str | None = None,
    outcome: str | None = None,
    solo_alertas: bool = False,
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Todas las llamadas, de todos los agentes. Es la lista de Reportes sin el resumen ni la IA."""
    return _pagina_reportes(
        request, db, desde, hasta, campaign_id, outcome, solo_alertas, page, extra={"vista": "historial"}
    )


def _info(request: Request, clave: str, titulo: str, lede: str, secciones: list[dict], pie: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "agentes_voz_info.html",
        {"clave": clave, "titulo": titulo, "lede": lede, "secciones": secciones, "pie": pie},
    )


_OK = {"clase": "completed", "texto": "Activo"}
_PRONTO = {"clase": "pending", "texto": "Pronto"}


@router.get("/numeros", response_class=HTMLResponse)
def numeros(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """Con qué número se llama. Hoy es uno solo, el de la troncal; acá se ve cómo está configurado."""
    total, ultima = db.execute(select(func.count(CallAttempt.id), func.max(CallAttempt.started_at))).one()
    zona = settings.timezone
    return _info(
        request,
        "numeros",
        "Números telefónicos",
        "Los números desde los que llaman los agentes.",
        [
            {
                "titulo": "Línea de salida",
                "nota": "Solo lectura",
                "filas": [
                    ("Identificador de llamada", settings.asterisk_callerid, None),
                    ("Cómo se marca", settings.asterisk_dial_template, None),
                    ("Llamadas hechas", str(total), None),
                    ("Última llamada", ultima.astimezone(zona).strftime("%d/%m/%Y %H:%M") if ultima else "Todavía ninguna", None),
                ],
            }
        ],
        "El número que ve el cliente lo asigna el proveedor de la troncal. Asignar un número distinto a cada "
        "agente se agrega en una próxima etapa.",
    )


@router.get("/troncales", response_class=HTMLResponse)
def troncales(request: Request) -> HTMLResponse:
    """Estado de la central telefónica y de sus troncales SIP, leído de Asterisk."""
    from app.services.asterisk_ari import AriClient, AriError

    central: list[tuple] = []
    troncales_sip: list[tuple] = []
    try:
        with AriClient(timeout=5.0) as ari:
            info = ari.ping()
            central.append(("Asterisk", info.get("system", {}).get("version", "?"), _OK))
            endpoints = ari.endpoints()
    except AriError as exc:
        log.warning("Troncales: %s", exc)
        central.append(("Asterisk", "No se pudo contactar la central telefónica", {"clase": "failed", "texto": "Sin conexión"}))
        endpoints = None
    for e in endpoints or []:
        if e.get("technology") != "PJSIP":
            continue
        estado = e.get("state", "unknown")
        troncales_sip.append(
            (
                e.get("resource", "?"),
                f"{len(e.get('channel_ids', []))} llamada(s) en curso",
                {"clase": "completed" if estado == "online" else "pending", "texto": estado},
            )
        )
    return _info(
        request,
        "troncales",
        "Troncales SIP",
        "Las conexiones de la central telefónica con el proveedor que lleva las llamadas.",
        [
            {"titulo": "Central telefónica", "filas": central},
            {
                "titulo": "Troncales y extensiones",
                "nota": "Leído de Asterisk",
                "filas": troncales_sip
                or [("Sin datos" if endpoints is None else "Sin troncales", "Asterisk no informa ninguna", None)],
            },
        ],
        "El detalle del registro con el proveedor está en Diagnóstico. Agregar o cambiar troncales desde el "
        "panel se agrega en una próxima etapa.",
    )


@router.get("/canales", response_class=HTMLResponse)
def canales(request: Request) -> HTMLResponse:
    return _info(
        request,
        "canales",
        "Canales",
        "Por dónde habla con los clientes cada agente.",
        [
            {
                "titulo": "Canales",
                "filas": [
                    ("Teléfono · llamadas salientes", "El agente llama por la troncal SIP", _OK),
                    ("Teléfono · llamadas entrantes", "Que el cliente pueda devolver la llamada al agente", _PRONTO),
                    ("WhatsApp", "Aviso si el cliente no atiende y agentes de texto", _PRONTO),
                ],
            }
        ],
    )


@router.post("/reportes/preguntar", response_class=HTMLResponse)
def reportes_preguntar(
    request: Request,
    pregunta: str = Form(...),
    desde: str = Form(""),
    hasta: str = Form(""),
    campaign_id: str = Form(""),
    outcome: str = Form(""),
    solo_alertas: bool = Form(False),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Claude Haiku responde sobre las llamadas que muestran los filtros (las más nuevas, hasta 200)."""
    pregunta = pregunta.strip()[:500]
    campaign = _entero(campaign_id)
    condiciones, _ = _filtros_reporte(db, desde or None, hasta or None, campaign, outcome or None, solo_alertas)
    llamadas = db.scalars(
        _con_joins(select(CallAttempt), condiciones)
        .where(CallAttempt.answered_at.is_not(None))
        .options(
            selectinload(CallAttempt.target).selectinload(SurveyTarget.campaign),
            selectinload(CallAttempt.analysis),
            selectinload(CallAttempt.answers).selectinload(Answer.question),
        )
        .order_by(CallAttempt.started_at.desc())
        .limit(preguntar.MAX_LLAMADAS)
    ).all()
    quien = getattr(getattr(request.state, "principal", None), "usuario", "") or "?"
    if pregunta and not preguntar.permitir(quien):
        respuesta, usadas = "Hiciste muchas preguntas seguidas. Probá de nuevo en un rato.", 0
        return _pagina_reportes(
            request, db, desde or None, hasta or None, campaign_id or None, outcome or None, solo_alertas, 1,
            extra={"pregunta": pregunta, "respuesta": respuesta, "usadas": usadas},
        )
    respuesta, usadas = preguntar.preguntar(pregunta, llamadas) if pregunta else (None, 0)
    return _pagina_reportes(
        request, db, desde or None, hasta or None, campaign_id or None, outcome or None, solo_alertas, 1,
        extra={
            "query_export": urlencode({k: v for k, v in {
                "desde": desde, "hasta": hasta, "campaign_id": campaign_id, "outcome": outcome,
                "solo_alertas": "true" if solo_alertas else "",
            }.items() if v}),
            "pregunta": pregunta,
            "respuesta": respuesta or "No se pudo consultar a Claude en este momento. Probá de nuevo en un rato.",
            "usadas": usadas,
        },
    )


def _pagina_reportes(
    request: Request,
    db: Session,
    desde: str | None,
    hasta: str | None,
    campaign_id: str | None,
    outcome: str | None,
    solo_alertas: bool,
    page: int,
    extra: dict | None = None,
) -> HTMLResponse:
    campaign = _entero(campaign_id)
    condiciones, descripcion = _filtros_reporte(
        db, desde, hasta, campaign, outcome, solo_alertas
    )

    total = db.scalar(_con_joins(select(func.count()), condiciones)) or 0

    resumen = db.execute(
        _con_joins(
            select(
                func.avg(CallAnalysis.satisfaction_score),
                func.count(CallAnalysis.id).filter(
                    CallAnalysis.requires_followup.is_(True)
                ),
                func.count(CallAttempt.id).filter(
                    CallAttempt.outcome == CallOutcome.COMPLETED
                ),
                func.avg(CallAttempt.duration_seconds),
            ),
            condiciones,
        )
    ).one()

    paginas = max(1, (total + POR_PAGINA - 1) // POR_PAGINA)
    page = max(1, min(page, paginas))

    calls = db.scalars(
        _con_joins(select(CallAttempt), condiciones)
        .options(
            selectinload(CallAttempt.target).selectinload(SurveyTarget.campaign),
            selectinload(CallAttempt.analysis),
            selectinload(CallAttempt.answers),
        )
        .order_by(CallAttempt.started_at.desc())
        .offset((page - 1) * POR_PAGINA)
        .limit(POR_PAGINA)
    ).all()

    # Embudo y mejor horario sobre TODO lo filtrado, no solo la página que se ve.
    intentos = db.execute(
        _con_joins(
            select(CallAttempt.started_at, CallAttempt.answered_at, CallAttempt.outcome), condiciones
        ).limit(20_000)
    ).all()

    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "contacto": contactabilidad.resumen([tuple(i) for i in intentos], settings.timezone),
            "calidades": {c.id: calidad.calidad_de(c) for c in calls},
            "calls": calls,
            "total": total,
            "page": page,
            "pages": paginas,
            "promedio": round(resumen[0], 1) if resumen[0] is not None else None,
            "alertas": resumen[1] or 0,
            "completadas": resumen[2] or 0,
            "duracion_media": int(resumen[3]) if resumen[3] is not None else None,
            "descripcion": descripcion,
            "campaigns": db.scalars(select(Campaign).order_by(Campaign.name)).all(),
            "outcomes": list(CallOutcome),
            "f": {
                "desde": desde or "",
                "hasta": hasta or "",
                "campaign_id": campaign,
                "outcome": outcome or "",
                "solo_alertas": solo_alertas,
            },
            # Sin `page`: el export no pagina, arrastrarlo daría a entender
            # que descarga solo la página que se está viendo.
            "query_export": _query_sin(request.query_params, "page"),
            # Para el paginador: los mismos filtros, listos para agregar &page=N
            "query_page": _query_sin(request.query_params, "page"),
            "satisfactory_min": SATISFACTORY_MIN,
            # Al preguntar (POST) los filtros llegan por formulario: extra trae su query para el export.
            **(extra or {}),
        },
    )


@router.get("/reportes/export.xlsx")
def exportar_reportes(
    desde: str | None = None,
    hasta: str | None = None,
    campaign_id: str | None = None,
    outcome: str | None = None,
    solo_alertas: bool = False,
    db: Session = Depends(get_db),
) -> Response:
    """Descarga los resultados filtrados como Excel.

    Sin paginar: el archivo trae todo lo que cumple el filtro, que es
    justamente para lo que se exporta.
    """
    condiciones, descripcion = _filtros_reporte(
        db, desde, hasta, _entero(campaign_id), outcome, solo_alertas
    )

    calls = db.scalars(
        _con_joins(select(CallAttempt), condiciones)
        .options(
            selectinload(CallAttempt.target).selectinload(SurveyTarget.campaign),
            selectinload(CallAttempt.analysis),
            selectinload(CallAttempt.answers).selectinload(Answer.question),
        )
        .order_by(CallAttempt.started_at.desc())
    ).all()

    contenido, truncado = construir_excel(
        list(calls), tz=settings.timezone, descripcion_filtros=descripcion
    )

    nombre = f"encuestas-{datetime.now(settings.timezone):%Y%m%d-%H%M}.xlsx"
    log.info(
        "Export de %d llamadas (%s)%s",
        len(calls), descripcion or "sin filtros", " [truncado]" if truncado else "",
    )

    return Response(
        content=contenido,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "Cache-Control": "no-store",
        },
    )


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
    db: Session = Depends(get_db),
) -> HTMLResponse:
    """Voces: la biblioteca para escuchar y, con VoiceStudio, clonar la voz de una persona."""
    lista, error_catalogo = voces.catalogo()
    en_uso: dict[str, list[str]] = {}
    for c in db.scalars(select(Campaign).order_by(Campaign.name)).all():
        en_uso.setdefault(c.voice_profile_id or settings.voicebox_profile_id or "piper", []).append(c.name)
    return templates.TemplateResponse(
        request,
        "voices.html",
        {
            "voces": lista,
            "en_uso": en_uso,
            "error_catalogo": error_catalogo,
            "puede_clonar": voicestudio.is_configured(),
            "elevenlabs": elevenlabs.is_configured(),
            "eleven_consumo": elevenlabs.consumo() if elevenlabs.is_configured() else None,
            "texto_referencia": voces.TEXTO_REFERENCIA,
            "max_referencia": int(voces.MAX_REFERENCIA_SEGUNDOS),
            "frase_permiso_plantilla": voces.frase_consentimiento("{persona}"),
            "pronunciaciones": pronunciaciones.lista(db),
            "voces_en_vivo": [v for v in lista if v["en_vivo"] and v["escuchar"]],
            "error": error,
            "ok": ok,
        },
    )


# Una grabación de 20 s en WAV de 48 kHz son ~2 MB: 15 MB alcanza para cualquier formato razonable.
MAX_AUDIO_BYTES = 15 * 1024 * 1024


@router.post("/voices/clonar")
async def clonar_voz(
    request: Request,
    nombre: str = Form(""),
    genero: str = Form(""),
    persona: str = Form(""),
    texto: str = Form(""),
    acepto: str = Form(""),
    referencia: UploadFile | None = File(None),
    consentimiento: UploadFile | None = File(None),
) -> JSONResponse:
    """Clona la voz de una persona que dio su permiso: grabación de referencia + su consentimiento grabado."""
    if not voicestudio.is_configured():
        return JSONResponse({"error": "El motor de voces clonadas todavía no está instalado en el servidor."}, 503)
    usuario = request.state.principal.usuario if getattr(request.state, "principal", None) else "?"
    nombre, persona, texto = nombre.strip()[:40], " ".join(persona.split())[:80], " ".join(texto.split())[:600]
    if not nombre or not persona:
        return JSONResponse({"error": "Poné el nombre de la voz y el de la persona que habla."}, 400)
    if genero not in ("Mujer", "Hombre"):
        return JSONResponse({"error": "Elegí si es voz de mujer o de hombre."}, 400)
    if acepto != "si":
        return JSONResponse({"error": "Sin el permiso de la persona no se puede clonar su voz."}, 400)
    if len(texto) < 20:
        return JSONResponse({"error": "Falta lo que se dice en la grabación, palabra por palabra."}, 400)
    if referencia is None or consentimiento is None:
        return JSONResponse({"error": "Faltan la grabación de la voz o la del permiso."}, 400)
    ref, perm = await referencia.read(), await consentimiento.read()
    if not ref or not perm:
        return JSONResponse({"error": "Una de las grabaciones está vacía. Grabala de nuevo."}, 400)
    if len(ref) > MAX_AUDIO_BYTES or len(perm) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "El audio es demasiado grande: alcanza con unos 5 segundos."}, 400)
    largo = voicestudio.duracion_wav(ref)
    if largo is not None and largo > voces.MAX_REFERENCIA_SEGUNDOS + 0.5:
        return JSONResponse(
            {"error": f"La grabación dura {largo:.0f} segundos y para clonar alcanza con unos 5 (como mucho {voces.MAX_REFERENCIA_SEGUNDOS:.0f}). Grabala de nuevo, más corta."}, 400
        )
    if not agentes.permitir(f"clonar:{usuario}"):
        return JSONResponse({"error": "Ya clonaste varias voces en la última hora. Probá más tarde."}, 429)

    fecha = datetime.now(ZoneInfo("America/Asuncion")).strftime("%d/%m/%Y %H:%M")
    permiso = (
        f"{voces.frase_consentimiento(persona)} Grabado en Callbot el {fecha} (hora de Paraguay); "
        f"cargado por {usuario}."
    )
    try:
        # httpx sincrónico: a un thread, que la API siga atendiendo mientras se procesa el audio.
        perfil = await asyncio.to_thread(
            voicestudio.clonar,
            f"{nombre} ({genero.lower()})",
            ref,
            referencia.filename or "referencia.wav",
            texto,
            perm,
            consentimiento.filename or "consentimiento.wav",
            permiso,
        )
    except voicestudio.VoiceStudioError as exc:
        return JSONResponse({"error": str(exc)}, 502)
    log.info("Voz clonada %s (%s) por %s", perfil.get("id"), nombre, usuario)
    return JSONResponse({"id": voicestudio.id_callbot(str(perfil["id"])), "nombre": nombre})


@router.post("/voices/disenar/probar")
async def disenar_probar(
    request: Request, genero: str = Form(""), edad: str = Form(""), tono: str = Form("")
) -> JSONResponse:
    """Inventa una voz con IA a partir de tres rasgos y devuelve el audio para escucharlo. No es la voz de
    nadie: no pide permiso. Cada pedido sale con una variante distinta."""
    if not voicestudio.is_configured():
        return JSONResponse({"error": "El motor de voz todavía no está instalado en el servidor."}, 503)
    instruccion = voces.instruccion_diseno(genero, edad, tono)
    if instruccion is None:
        return JSONResponse({"error": "Elegí si es voz de mujer o de hombre, la edad y el tono."}, 400)
    usuario = request.state.principal.usuario if getattr(request.state, "principal", None) else "?"
    if not agentes.permitir_diseno(usuario):
        return JSONResponse({"error": "Probaste muchas voces en la última hora. Seguí con una de las que escuchaste o probá más tarde."}, 429)
    try:
        wav = await asyncio.to_thread(
            voicestudio.disenar, instruccion, secrets.randbelow(1_000_000), voces.TEXTO_DISENO
        )
    except voicestudio.VoiceStudioError as exc:
        return JSONResponse({"error": str(exc)}, 502)
    largo = voicestudio.duracion_wav(wav)
    if not wav or largo is None or not 2.0 <= largo <= voces.MAX_REFERENCIA_SEGUNDOS:
        return JSONResponse({"error": "Esa variante salió mal. Probá otra."}, 502)
    codigo = voces.guardar_muestra(wav)
    return JSONResponse({"codigo": codigo, "audio": f"/voices/disenar/muestra/{codigo}"})


@router.get("/voices/disenar/muestra/{codigo}")
def disenar_muestra(codigo: str) -> Response:
    wav = voces.muestra(codigo)
    if wav is None:
        raise HTTPException(404, "Esa muestra venció: probá la voz de nuevo.")
    return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/voices/disenar/guardar")
async def disenar_guardar(
    request: Request, codigo: str = Form(""), nombre: str = Form(""), genero: str = Form("")
) -> JSONResponse:
    """Se queda con la voz que se escuchó: el mismo audio pasa a ser su referencia, y suena igual en todas las frases."""
    if not voicestudio.is_configured():
        return JSONResponse({"error": "El motor de voz todavía no está instalado en el servidor."}, 503)
    nombre = " ".join(nombre.split())[:40]
    if not nombre:
        return JSONResponse({"error": "Ponele un nombre a la voz."}, 400)
    if genero not in voces.DISENO_GENERO:
        return JSONResponse({"error": "Elegí si es voz de mujer o de hombre."}, 400)
    wav = voces.muestra(codigo)
    if wav is None:
        return JSONResponse({"error": "Esa muestra venció: probá la voz de nuevo y guardala."}, 410)
    usuario = request.state.principal.usuario if getattr(request.state, "principal", None) else "?"
    if not agentes.permitir_voz_ia(usuario):
        return JSONResponse({"error": "Ya guardaste varias voces en la última hora. Probá más tarde."}, 429)
    try:
        perfil = await asyncio.to_thread(
            voicestudio.crear_disenada, f"{nombre} ({genero.lower()}, IA)", wav, voces.TEXTO_DISENO
        )
    except voicestudio.VoiceStudioError as exc:
        return JSONResponse({"error": str(exc)}, 502)
    voces.quitar_muestra(codigo)
    log.info("Voz creada con IA %s (%s) por %s", perfil.get("id"), nombre, usuario)
    return JSONResponse({"id": voicestudio.id_callbot(str(perfil["id"])), "nombre": nombre})


@router.post("/voices/{voz_id}/borrar")
def borrar_voz(voz_id: str, db: Session = Depends(get_db)) -> RedirectResponse:
    """Borra una voz clonada (por ejemplo, si la persona retira su permiso). Las campañas que la
    usaban pasan a la voz del servidor."""
    perfil = voicestudio.id_estudio(voz_id)
    if not perfil or not re.fullmatch(r"[A-Za-z0-9_-]{1,61}", perfil):
        return _redirect(f"/voices?error={quote_plus('Solo se pueden borrar las voces clonadas')}")
    try:
        voicestudio.borrar(perfil)
    except voicestudio.VoiceStudioError as exc:
        return _redirect(f"/voices?error={quote_plus(str(exc))}")
    campanas = db.scalars(select(Campaign).where(Campaign.voice_profile_id == voz_id)).all()
    for c in campanas:
        c.voice_profile_id = None
    db.commit()
    aviso = "Voz borrada"
    if campanas:
        aviso += f". {len(campanas)} agente(s) pasan a la voz del servidor"
    return _redirect(f"/voices?ok={quote_plus(aviso)}")


@router.get("/voices/preview")
def preview_voice(
    profile_id: str, text: str | None = None, literal: int = 0, db: Session = Depends(get_db)
) -> Response:
    """Genera y devuelve un WAV para escuchar en el navegador. Aplica el diccionario de pronunciación, para oír lo
    que va a decir el bot; con `literal=1` el texto se dice tal cual (es lo que usa la prueba de una palabra)."""
    sample = (text or "").strip()[:300] or (
        "Hola, buenos días. Le hablamos de Santa Rosa Automotores. "
        "¿Tiene un minuto para una consulta muy breve?"
    )
    if not literal:
        sample = pronunciaciones.aplicar(sample, pronunciaciones.lista(db))
    perfil = voicestudio.id_estudio(profile_id)
    eleven = elevenlabs.id_elevenlabs(profile_id)
    media = "audio/wav"
    try:
        if eleven:
            wav, media = elevenlabs.muestra(eleven, sample), "audio/mpeg"
        elif perfil:
            wav = voicestudio.muestra(perfil, sample)
        else:
            wav = voicebox.generate(profile_id, sample)
    except (voicebox.VoiceboxError, voicestudio.VoiceStudioError, elevenlabs.ElevenLabsError) as exc:
        raise HTTPException(502, str(exc))

    return Response(
        content=wav,
        media_type=media,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/voices/campaign/{campaign_id}")
def update_campaign_voice(
    request: Request,
    campaign_id: int,
    voice_speed: float = Form(1.0),
    voice_pitch: float = Form(1.0),
    voice_expressiveness: float = Form(0.667),
    voice_volume: float = Form(1.0),
    voice_profile_id: str = Form(""),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Guarda la voz y su modulación, desde la página de Voz.

    Los mismos controles están en el detalle de la campaña, pero ahí quedan
    abajo del guion y nadie los encuentra: el lugar donde uno busca cómo suena
    el bot es la pestaña que se llama Voz.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Campaña inexistente")
    antes = versiones.instantanea(campaign)

    campaign.voice_speed = min(max(voice_speed, 0.5), 2.0)
    campaign.voice_pitch = min(max(voice_pitch, 0.7), 1.4)
    campaign.voice_expressiveness = min(max(voice_expressiveness, 0.0), 1.5)
    campaign.voice_volume = min(max(voice_volume, 0.2), 1.5)

    # "" = la del servidor, "piper" = Piper a propósito, o el id de un perfil de Voicebox.
    perfil = voice_profile_id.strip()
    if perfil and perfil != "piper" and not re.fullmatch(r"((vs|el):)?[A-Za-z0-9_-]{1,61}", perfil):
        return _redirect(f"/voices?error={quote_plus('Voz inválida')}")
    campaign.voice_profile_id = perfil or None
    principal = getattr(request.state, "principal", None)
    versiones.registrar(db, campaign, antes, principal.usuario if principal else None)
    db.commit()

    return _redirect(f"/voices?ok=Voz+actualizada+en+{quote_plus(campaign.name)}")


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
    """Diagnóstico: verifica Bitrix, Asterisk, Claude y Voicebox de una sola pasada."""
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

    # Claude: no se hace un pedido real (cada uno cuesta), solo se dice si hay clave y qué modelo.
    from app.services import llm

    checks.append(
        {
            "name": "Claude (LLM)",
            "ok": True,
            # Sin clave el bot funciona, pero peor: no es una falla y tampoco está bien.
            "aviso": not llm.is_configured(),
            "detail": (
                f"{settings.llm_model}: conversa, analiza, redacta instrucciones y responde consultas"
                if llm.is_configured()
                else "sin clave (ANTHROPIC_API_KEY): el bot usa frases fijas y no hay análisis ni IA en el panel"
            ),
        }
    )

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
                    "detail": f"{str(exc)[:160]}: el bot va a hablar con Piper",
                }
            )

    # VoiceStudio (voces clonadas en vivo). Sin instalar no es falla: clonar figura como «Pronto».
    if not voicestudio.is_configured():
        checks.append({"name": "VoiceStudio (clonar voces)", "ok": True, "detail": "no instalado; clonar voces figura como «Pronto»"})
    else:
        try:
            perfiles = voicestudio.list_profiles()
            n = sum(1 for p in perfiles if p.get("verified_own_voice") and p.get("kind", "clone") == "clone")
            checks.append({"name": "VoiceStudio (clonar voces)", "ok": True,
                           "detail": f"responde; {n} {'voz clonada' if n == 1 else 'voces clonadas'} con permiso"})
        except voicestudio.VoiceStudioError as exc:
            checks.append({"name": "VoiceStudio (clonar voces)", "ok": False,
                           "detail": f"{exc} Las voces clonadas hablan con la voz del servidor."})

    # ElevenLabs (voces de la cuenta, de pago). Sin clave no es falla: solo no se ofrecen esas voces.
    if not elevenlabs.is_configured():
        checks.append({"name": "ElevenLabs (voces de la cuenta)", "ok": True, "detail": "no conectado; solo hay voces del servidor"})
    else:
        try:
            n = sum(1 for v in elevenlabs.listar_voces() if v.get("voice_id"))
            c = elevenlabs.consumo()
            uso = f"; usaste {c['usados']:,} de {c['limite']:,} caracteres".replace(",", ".") if c and c["limite"] else ""
            checks.append({"name": "ElevenLabs (voces de la cuenta)", "ok": True, "detail": f"responde; {n} voces en la cuenta{uso}"})
        except elevenlabs.ElevenLabsError as exc:
            checks.append({"name": "ElevenLabs (voces de la cuenta)", "ok": False, "detail": f"{exc} Las voces de ElevenLabs hablan con la voz del servidor."})

    dias = parse_days(settings.call_window_days)
    return templates.TemplateResponse(
        request, "health.html", {"checks": checks, "settings": settings, "dias_texto": texto_dias(dias)}
    )
