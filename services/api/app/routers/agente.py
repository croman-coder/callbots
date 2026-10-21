"""Ficha del agente de voz: pestañas, guardado, instrucciones con IA y la prueba desde el navegador.

Las pestañas copian el orden de Dapta (ver docs/referencia-dapta.md): Contexto, Probar agente,
Configuración, Análisis de llamadas, Registro de llamadas, Métrica y Canales. Cada una se arma en el
servidor; las que editan algo comparten el botón «Guardar cambios» de la cabecera.

La prueba desde el navegador no pasa por la telefonía: el micrófono manda audio por un WebSocket, la
API lo reenvía al voice-agent hablando el mismo protocolo que Asterisk (AudioSocket) y el voice-agent
devuelve la voz del bot y, solo en estas sesiones, la transcripción en vivo.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time as reloj
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session, selectinload

from app import agentes, voces
from app.bitrix.client import normalize_phone_py
from app.config import settings
from app.db import get_db, session_scope
from app.deps import principal_ws, require_admin
from app.models import (
    Answer,
    CallAnalysis,
    CallAttempt,
    CallOutcome,
    Campaign,
    PruebaAgente,
    Question,
    QuestionType,
    SurveyTarget,
)
from app.plantillas import templates
from app.routers.internal import sesion_de_prueba
from app.scheduling import parse_days
from app.services import calidad
from app.services.scoring import SATISFACTORY_MIN

log = logging.getLogger(__name__)

router = APIRouter(tags=["agente"], dependencies=[Depends(require_admin)])
ws_router = APIRouter(tags=["agente"])

PESTANAS = (
    ("contexto", "Contexto"),
    ("probar", "Probar agente"),
    ("configuracion", "Configuración"),
    ("analisis", "Análisis de llamadas"),
    ("registro", "Registro de llamadas"),
    ("metrica", "Métrica"),
    ("canales", "Canales"),
)
# Las que tienen algo que guardar con «Guardar cambios».
CON_FORMULARIO = {"contexto", "probar", "configuracion", "analisis"}

ETIQUETAS_TIPO = {
    QuestionType.SCALE_1_10: "escala 0-10",
    QuestionType.SCALE_1_5: "escala 1-5 (heredada)",
    QuestionType.YES_NO: "sí / no",
    QuestionType.NUMERIC: "número",
    QuestionType.OPEN: "respuesta libre",
}
DIAS_SEMANA = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
PERIODOS = {"7": 7, "30": 30, "90": 90, "todo": None}
POR_PAGINA = 20


def _agente(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Ese agente no existe")
    return campaign


def _volver(campaign_id: int, tab: str, **extra: str) -> RedirectResponse:
    tab = tab if tab in dict(PESTANAS) else "contexto"
    q = "&".join(f"{k}={v}" for k, v in extra.items() if v)
    return RedirectResponse(f"/campaigns/{campaign_id}?tab={tab}{'&' + q if q else ''}", status_code=303)


# ---------------------------------------------------------------------------
# Datos de cada pestaña
# ---------------------------------------------------------------------------
def _datos_probar(db: Session, campaign: Campaign) -> dict:
    pruebas = db.scalars(
        select(PruebaAgente)
        .where(PruebaAgente.campaign_id == campaign.id)
        .order_by(PruebaAgente.iniciada_en.desc())
        .limit(8)
    ).all()
    return {"pruebas": pruebas}


def _datos_configuracion(db: Session, campaign: Campaign) -> dict:
    lista, error_voces = voces.catalogo()
    saludo = (campaign.intro_script or "").replace("{nombre}", "Marta")
    return {
        "voces": lista,
        "voz_actual": voces.elegida(lista, campaign.voice_profile_id),
        "error_voces": error_voces,
        "saludo_prueba": saludo[:400],
        "dias_activos": parse_days(campaign.call_window_days),
        "dias_semana": DIAS_SEMANA,
        "tipos": list(QuestionType),
        "etiquetas_tipo": ETIQUETAS_TIPO,
        "modelo_llm": settings.llm_model,
    }


def _datos_registro(db: Session, campaign: Campaign, filtro: str, page: int) -> dict:
    filtro = filtro if filtro in ("todas", "conectadas", "exitosas") else "todas"
    condiciones = [SurveyTarget.campaign_id == campaign.id]
    if filtro == "conectadas":
        condiciones.append(CallAttempt.answered_at.is_not(None))
    elif filtro == "exitosas":
        condiciones.append(CallAttempt.outcome == CallOutcome.COMPLETED)
    base = select(CallAttempt).join(SurveyTarget, SurveyTarget.id == CallAttempt.target_id).where(*condiciones)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    paginas = max(1, (total + POR_PAGINA - 1) // POR_PAGINA)
    page = max(1, min(page, paginas))
    llamadas = db.scalars(
        base.options(
            selectinload(CallAttempt.target),
            selectinload(CallAttempt.analysis),
            selectinload(CallAttempt.answers),
        )
        .order_by(CallAttempt.started_at.desc().nulls_last(), CallAttempt.id.desc())
        .offset((page - 1) * POR_PAGINA)
        .limit(POR_PAGINA)
    ).all()
    return {
        "filtro": filtro,
        "llamadas": llamadas,
        "calidades": {c.id: calidad.calidad_de(c) for c in llamadas},
        "total": total,
        "page": page,
        "paginas": paginas,
    }


def _datos_metrica(db: Session, campaign: Campaign, dias: str) -> dict:
    dias = dias if dias in PERIODOS else "30"
    desde = datetime.now(timezone.utc) - timedelta(days=PERIODOS[dias]) if PERIODOS[dias] else None
    zona = settings.timezone

    con_llamadas = [SurveyTarget.campaign_id == campaign.id]
    if desde:
        con_llamadas.append(CallAttempt.started_at >= desde)
    unir = lambda q: q.join(SurveyTarget, SurveyTarget.id == CallAttempt.target_id).where(*con_llamadas)  # noqa: E731

    total, atendidas, contactos_llamados, conectados, metas, duracion = db.execute(
        unir(
            select(
                func.count(CallAttempt.id),
                func.count(CallAttempt.id).filter(CallAttempt.answered_at.is_not(None)),
                func.count(distinct(SurveyTarget.id)),
                func.count(distinct(SurveyTarget.id)).filter(CallAttempt.answered_at.is_not(None)),
                func.count(distinct(SurveyTarget.id)).filter(CallAttempt.outcome == CallOutcome.COMPLETED),
                func.avg(CallAttempt.duration_seconds).filter(CallAttempt.answered_at.is_not(None)),
            )
        )
    ).one()

    satisfaccion = db.scalar(
        select(func.avg(CallAnalysis.satisfaction_score))
        .select_from(CallAttempt)
        .join(CallAnalysis, CallAnalysis.call_id == CallAttempt.id)
        .join(SurveyTarget, SurveyTarget.id == CallAttempt.target_id)
        .where(*con_llamadas)
    )

    contactos_q = select(func.count(SurveyTarget.id)).where(SurveyTarget.campaign_id == campaign.id)
    if desde:
        contactos_q = contactos_q.where(SurveyTarget.created_at >= desde)
    contactos = db.scalar(contactos_q) or 0

    # Llamadas por día: se agrupa en Python para no depender de funciones de fecha de una base en particular.
    por_dia: dict[str, list[int]] = {}
    for inicio, atendio in db.execute(
        unir(select(CallAttempt.started_at, CallAttempt.answered_at)).where(CallAttempt.started_at.is_not(None)).limit(20_000)
    ).all():
        dia = inicio.astimezone(zona).strftime("%d/%m")
        fila = por_dia.setdefault(dia, [0, 0])
        fila[0] += 1
        fila[1] += 1 if atendio else 0
    dias_lista = [{"dia": d, "llamadas": v[0], "atendidas": v[1]} for d, v in por_dia.items()]
    dias_lista.sort(key=lambda x: (x["dia"][3:], x["dia"][:2]))
    dias_lista = dias_lista[-31:]
    maximo = max((d["llamadas"] for d in dias_lista), default=0)

    por_pregunta = db.execute(
        select(Question.position, Question.text, func.avg(Answer.value_numeric), func.count(Answer.id))
        .join(Answer, Answer.question_id == Question.id)
        .join(CallAttempt, CallAttempt.id == Answer.call_id)
        .where(
            Question.campaign_id == campaign.id,
            Answer.value_numeric.is_not(None),
            *([CallAttempt.started_at >= desde] if desde else []),
        )
        .group_by(Question.id, Question.position, Question.text)
        .order_by(Question.position)
    ).all()

    def pct(a: int, b: int) -> int:
        return round(100 * a / b) if b else 0

    return {
        "dias": dias,
        "periodos": {"7": "Últimos 7 días", "30": "Últimos 30 días", "90": "Últimos 90 días", "todo": "Todo"},
        "kpi": {
            "total": total or 0,
            "contactos_llamados": contactos_llamados or 0,
            "tasa_conexion": pct(atendidas or 0, total or 0),
            "duracion": int(duracion) if duracion is not None else None,
            "metas": metas or 0,
            "satisfaccion": round(satisfaccion, 1) if satisfaccion is not None else None,
        },
        "por_dia": dias_lista,
        "maximo_dia": maximo,
        "embudo": [
            {"titulo": "Contactos", "valor": contactos, "pct": 100 if contactos else 0},
            {"titulo": "Conectados", "valor": conectados or 0, "pct": pct(conectados or 0, contactos)},
            {"titulo": "Metas logradas", "valor": metas or 0, "pct": pct(metas or 0, contactos)},
        ],
        "por_pregunta": [
            {"pos": pos, "texto": texto, "promedio": round(prom, 1), "n": n} for pos, texto, prom, n in por_pregunta
        ],
        "satisfactory_min": SATISFACTORY_MIN,
    }


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def ficha(
    request: Request,
    campaign_id: int,
    tab: str = "contexto",
    filtro: str = "todas",
    page: int = 1,
    dias: str = "30",
    nuevo: int = 0,
    error: str | None = None,
    ok: str | None = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    campaign = _agente(db, campaign_id)
    tab = tab if tab in dict(PESTANAS) else "contexto"
    activas = [q for q in campaign.questions if q.is_active]
    ctx: dict = {
        "campaign": campaign,
        "pestanas": PESTANAS,
        "tab": tab,
        "con_formulario": tab in CON_FORMULARIO,
        "nuevo": bool(nuevo),
        "error": error,
        "ok": ok,
        "preguntas_activas": len(activas),
        "tokens": agentes.estimar_tokens(campaign.conversation_prompt),
        "propositos": agentes.PROPOSITOS,
        "callerid": settings.asterisk_callerid,
        "tz": settings.timezone,
        "bitrix_comentario": settings.bitrix_timeline_comment and bool(settings.bitrix_webhook_url),
    }
    if tab == "probar":
        ctx.update(_datos_probar(db, campaign))
    elif tab == "configuracion":
        ctx.update(_datos_configuracion(db, campaign))
    elif tab == "registro":
        ctx.update(_datos_registro(db, campaign, filtro, page))
    elif tab == "metrica":
        ctx.update(_datos_metrica(db, campaign, dias))
    return templates.TemplateResponse(request, "agente.html", ctx)


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------
def _hora(valor: str | None) -> str | None:
    if valor is None:
        return None
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", valor.strip()):
        raise HTTPException(400, "La hora tiene que ser HH:MM")
    return valor.strip()


def _entre(valor: float | int | None, minimo, maximo):
    return None if valor is None else min(max(valor, minimo), maximo)


@router.post("/campaigns/{campaign_id}/guardar")
async def guardar(campaign_id: int, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Guarda lo que venga en el formulario de la pestaña. Lo que no viene no se toca: cada pestaña
    manda solo sus campos. Se lee el formulario a mano porque FastAPI convierte un campo vacío en
    None, y acá vacío quiere decir «borralo» (por ejemplo, las instrucciones del análisis)."""
    form = await request.form()
    tab = str(form.get("tab", "contexto"))
    campaign = _agente(db, campaign_id)

    def texto(campo: str, maximo: int) -> str | None:
        return str(form[campo]).strip()[:maximo] if campo in form else None

    def numero(campo: str, tipo=int):
        if campo not in form or str(form[campo]).strip() == "":
            return None
        try:
            return tipo(str(form[campo]).strip())
        except ValueError:
            raise HTTPException(400, f"Valor inválido en {campo}") from None

    name = texto("name", 160)
    if name is not None:
        if not name:
            return _volver(campaign_id, tab, error="El agente necesita un nombre")
        campaign.name = name
    description = texto("description", 500)
    if description is not None:
        campaign.description = description or None
    for campo, maximo in (
        ("empresa_nombre", 160),
        ("empresa_descripcion", 4000),
        ("instrucciones_importantes", 4000),
        ("conversation_prompt", 20000),
        ("analysis_prompt", 8000),
    ):
        limpio = texto(campo, maximo)
        if limpio is not None:
            setattr(campaign, campo, limpio or None)

    for campo in ("intro_script", "outro_script", "fallback_script", "optout_script"):
        limpio = texto(campo, 2000)
        if limpio is not None:
            if not limpio:
                return _volver(campaign_id, tab, error="El guion no puede quedar con frases vacías")
            setattr(campaign, campo, limpio)

    try:
        inicio, fin = _hora(texto("call_window_start", 5)), _hora(texto("call_window_end", 5))
    except HTTPException:
        return _volver(campaign_id, tab, error="Revisá el horario: va como HH:MM, por ejemplo 09:00")
    if inicio:
        campaign.call_window_start = inicio
    if fin:
        campaign.call_window_end = fin
    if campaign.call_window_start >= campaign.call_window_end:
        return _volver(campaign_id, tab, error="El horario tiene que terminar después de empezar")
    if "dias_enviados" in form:
        validos = sorted({int(d) for d in form.getlist("dias") if str(d).isdigit() and 0 <= int(d) <= 6})
        if not validos:
            return _volver(campaign_id, tab, error="Elegí al menos un día para llamar")
        campaign.call_window_days = ",".join(str(d) for d in validos)
    try:
        enteros = {c: numero(c) for c in ("max_attempts", "retry_interval_minutes", "delay_hours", "bitrix_entity_type_id")}
        reales = {c: numero(c, float) for c in ("voice_speed", "voice_pitch", "voice_expressiveness", "voice_volume")}
    except HTTPException:
        return _volver(campaign_id, tab, error="Hay un número mal escrito: revisá intentos, reintento, demora y voz")
    for campo, minimo, maximo in (("max_attempts", 1, 10), ("retry_interval_minutes", 5, 1440), ("delay_hours", 0, 720)):
        if enteros[campo] is not None:
            setattr(campaign, campo, _entre(enteros[campo], minimo, maximo))
    entidad = enteros["bitrix_entity_type_id"]
    if entidad is not None:
        campaign.bitrix_entity_type_id = entidad
    disparador = texto("trigger_field", 120)
    if disparador:
        campaign.trigger_field = disparador

    perfil = texto("voice_profile_id", 64)
    if perfil is not None:
        if perfil and perfil != "piper" and not re.fullmatch(r"(vs:)?[A-Za-z0-9_-]{1,61}", perfil):
            return _volver(campaign_id, tab, error="Esa voz no existe: elegí una de la lista")
        campaign.voice_profile_id = perfil or None
    # Mismos topes que en Voz: un valor absurdo deja la voz irreconocible o tarda una eternidad.
    for campo, minimo, maximo in (
        ("voice_speed", 0.5, 2.0),
        ("voice_pitch", 0.7, 1.4),
        ("voice_expressiveness", 0.0, 1.5),
        ("voice_volume", 0.2, 1.5),
    ):
        if reales[campo] is not None:
            setattr(campaign, campo, _entre(reales[campo], minimo, maximo))

    # Buzón de voz: "" = no detectar. Solo se tocan los campos que llegaron (cada pestaña manda los suyos).
    accion = texto("voicemail_action", 10)
    if accion is not None:
        if accion not in ("", "colgar", "mensaje"):
            return _volver(campaign_id, tab, error="Elegí qué hacer con el buzón de voz de la lista")
        campaign.voicemail_action = accion or None
    mensaje = texto("voicemail_script", 600)
    if mensaje is not None:
        if not mensaje and campaign.voicemail_action == "mensaje":
            return _volver(campaign_id, tab, error="Escribí el mensaje que se deja en el buzón de voz")
        if mensaje:
            campaign.voicemail_script = mensaje

    # Transferencia a un asesor: el número se guarda normalizado (+595…); vacío = sin transferencia.
    asesor = texto("transfer_number", 30)
    if asesor is not None:
        if asesor:
            normal = normalize_phone_py(asesor)
            if not normal or not re.fullmatch(r"\+\d{9,15}", normal):
                return _volver(campaign_id, tab, error="El teléfono del asesor no parece válido: probá con 0981 123 456")
            campaign.transfer_number = normal
        else:
            campaign.transfer_number = None
    frase = texto("transfer_script", 400)
    if frase:
        campaign.transfer_script = frase

    db.commit()
    return _volver(campaign_id, tab, ok="Cambios guardados")


@router.post("/campaigns/{campaign_id}/estado")
def cambiar_estado(
    campaign_id: int, activo: bool = Form(...), tab: str = Form("contexto"), db: Session = Depends(get_db)
) -> RedirectResponse:
    campaign = _agente(db, campaign_id)
    if activo and not any(q.is_active for q in campaign.questions):
        return _volver(campaign_id, "configuracion", error="Agregá al menos una pregunta antes de activarlo")
    campaign.is_active = activo
    db.commit()
    log.info("Agente %s %s", campaign_id, "activado" if activo else "pausado")
    return _volver(campaign_id, tab, ok="Agente activado: ya puede llamar" if activo else "Agente pausado")


# ---------------------------------------------------------------------------
# Instrucciones con IA y valoración de pruebas
# ---------------------------------------------------------------------------
@router.post("/campaigns/{campaign_id}/instrucciones")
async def instrucciones_con_ia(
    request: Request,
    campaign_id: int,
    actual: str = Form(""),
    pedido: str = Form(""),
    name: str | None = Form(None),
    description: str | None = Form(None),
    empresa_nombre: str | None = Form(None),
    empresa_descripcion: str | None = Form(None),
    instrucciones_importantes: str | None = Form(None),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """«Generar con IA» (sin texto actual) o «Mejorar con IA». No guarda: el texto vuelve al editor y
    se guarda con «Guardar cambios», así quien lo pidió lo lee antes."""
    campaign = _agente(db, campaign_id)
    usuario = request.state.principal.usuario if getattr(request.state, "principal", None) else "?"
    if not agentes.permitir_ia(usuario):
        return JSONResponse({"error": "Ya usaste la IA muchas veces en la última hora. Probá más tarde."}, 429)

    def valor(form: str | None, guardado: str | None) -> str:
        return (form if form is not None else guardado or "").strip()[:4000]

    try:
        texto = await agentes.generar_instrucciones(
            nombre=valor(name, campaign.name),
            proposito=valor(description, campaign.description),
            empresa=valor(empresa_nombre, campaign.empresa_nombre),
            descripcion=valor(empresa_descripcion, campaign.empresa_descripcion),
            instrucciones=valor(instrucciones_importantes, campaign.instrucciones_importantes),
            intro=campaign.intro_script,
            preguntas=[q.text for q in campaign.questions if q.is_active],
            actual=actual[:20000],
            pedido=pedido[:500],
        )
    except agentes.SitioInvalido as exc:
        return JSONResponse({"error": str(exc)}, 502)
    return JSONResponse({"markdown": texto, "tokens": agentes.estimar_tokens(texto)})


@router.post("/campaigns/{campaign_id}/pruebas/{prueba_id}/valoracion")
def valorar_prueba(
    campaign_id: int, prueba_id: int, valoracion: str = Form(...), db: Session = Depends(get_db)
) -> JSONResponse:
    prueba = db.get(PruebaAgente, prueba_id)
    if prueba is None or prueba.campaign_id != campaign_id or valoracion not in ("bien", "mal"):
        raise HTTPException(404)
    prueba.valoracion = valoracion
    db.commit()
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Prueba desde el navegador: WebSocket <-> AudioSocket
# ---------------------------------------------------------------------------
TIPO_TERMINAR, TIPO_UUID, TIPO_AUDIO, TIPO_EVENTO = 0x00, 0x01, 0x10, 0x20
BYTES_TRAMA = 320  # 20 ms de PCM 8 kHz 16 bits
MAX_PRUEBAS_SIMULTANEAS = 2
MAX_SEGUNDOS = 300
_en_curso = 0


def trama(tipo: int, payload: bytes = b"") -> bytes:
    return bytes([tipo]) + len(payload).to_bytes(2, "big") + payload


def _origen_valido(ws: WebSocket) -> bool:
    """Un WebSocket no tiene la protección de CORS: sin esto, cualquier página que el usuario visite
    podría abrir la prueba con su sesión. El navegador siempre manda Origin y no se puede falsear."""
    origen = ws.headers.get("origin", "")
    host = ws.headers.get("host", "")
    return bool(origen) and re.sub(r"^https?://", "", origen).rstrip("/") == host


@ws_router.websocket("/ws/prueba/{campaign_id}")
async def prueba_web(ws: WebSocket, campaign_id: int) -> None:
    global _en_curso
    principal = principal_ws(ws)
    if principal is None or not _origen_valido(ws):
        await ws.close(code=1008)
        return

    with session_scope() as db:
        campaign = db.get(Campaign, campaign_id)
        if campaign is None:
            await ws.close(code=1008)
            return
        if not any(q.is_active for q in campaign.questions):
            await ws.accept()
            await ws.send_text(json.dumps({"tipo": "error", "texto": "El agente no tiene preguntas activas."}))
            await ws.close()
            return
        prueba = PruebaAgente(campaign_id=campaign_id, usuario=principal.usuario[:160], transcripcion=[])
        db.add(prueba)
        db.flush()
        prueba_id = prueba.id

    await ws.accept()
    if _en_curso >= MAX_PRUEBAS_SIMULTANEAS:
        await ws.send_text(json.dumps({"tipo": "error", "texto": "Ya hay otra prueba en curso. Probá en un rato."}))
        await ws.close()
        return

    _en_curso += 1
    transcripcion: list[dict] = []
    inicio = reloj.monotonic()
    writer = None
    try:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.voice_agent_host, settings.voice_agent_port), timeout=5
            )
        except (OSError, asyncio.TimeoutError):
            log.warning("Prueba web: no se pudo conectar con el voice-agent")
            await ws.send_text(json.dumps({"tipo": "error", "texto": "El agente de voz no está disponible ahora."}))
            return

        writer.write(trama(TIPO_UUID, sesion_de_prueba(campaign_id).bytes))
        await writer.drain()
        await ws.send_text(json.dumps({"tipo": "inicio", "prueba": prueba_id}))
        log.info("Prueba web del agente %s por %s", campaign_id, principal.usuario)

        async def del_navegador() -> None:
            resto = b""
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("bytes"):
                    resto += msg["bytes"]
                    while len(resto) >= BYTES_TRAMA:
                        writer.write(trama(TIPO_AUDIO, resto[:BYTES_TRAMA]))
                        resto = resto[BYTES_TRAMA:]
                    await writer.drain()
                elif msg.get("text") and '"colgar"' in msg["text"]:
                    return

        async def del_agente() -> None:
            while True:
                cabecera = await reader.readexactly(3)
                tipo, largo = cabecera[0], int.from_bytes(cabecera[1:3], "big")
                payload = await reader.readexactly(largo) if largo else b""
                if tipo == TIPO_TERMINAR:
                    return
                if tipo == TIPO_AUDIO:
                    await ws.send_bytes(payload)
                elif tipo == TIPO_EVENTO:
                    try:
                        evento = json.loads(payload.decode("utf-8"))
                    except ValueError:
                        continue
                    transcripcion.append({"rol": evento.get("rol"), "texto": evento.get("texto")})
                    await ws.send_text(json.dumps({"tipo": "texto", **evento}, ensure_ascii=False))

        tareas = [asyncio.create_task(del_navegador()), asyncio.create_task(del_agente())]
        hechas, pendientes = await asyncio.wait(tareas, timeout=MAX_SEGUNDOS, return_when=asyncio.FIRST_COMPLETED)
        for t in pendientes:
            t.cancel()
        for t in hechas:
            exc = t.exception()
            if exc and not isinstance(exc, (asyncio.IncompleteReadError, ConnectionError, WebSocketDisconnect)):
                log.warning("Prueba web terminó con error: %r", exc)
    finally:
        _en_curso -= 1
        if writer is not None:
            try:
                writer.write(trama(TIPO_TERMINAR))
                await writer.drain()
            except (ConnectionError, RuntimeError):
                pass
            writer.close()
        with session_scope() as db:
            prueba = db.get(PruebaAgente, prueba_id)
            if prueba is not None:
                prueba.transcripcion = transcripcion[-400:]
                prueba.duracion_seconds = int(reloj.monotonic() - inicio)
        try:
            await ws.send_text(json.dumps({"tipo": "fin", "prueba": prueba_id}))
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass
