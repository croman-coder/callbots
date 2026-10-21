"""Casos: la bandeja de lo que entró por los agentes de recepción, y la prueba por chat y simulación.

La bandeja reemplaza la planilla compartida del call center: cada caso dice qué pidió el cliente, a qué área va, quién
es el responsable y para cuándo tiene que estar contestado. Calidad ve arriba lo vencido y lo urgente.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import agentes, recepcion
from app.db import get_db
from app.deps import require_admin
from app.models import Campaign, Caso
from app.plantillas import templates
from app.routers.internal import avisar_caso, conocimiento_del_caso
from app.services import llm

log = logging.getLogger(__name__)

router = APIRouter(tags=["casos"], dependencies=[Depends(require_admin)])

FILTROS = {
    "pendientes": "Pendientes",
    "vencidos": "Vencidos",
    "respondidos": "Respondidos",
    "resueltos": "Resueltos por el agente",
    "cerrados": "Cerrados",
    "todos": "Todos",
}
MAX_LISTA = 200
MAX_VUELTAS_SIMULACION = 10


def _usuario(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.usuario if principal else "?"


def _volver(caso_id: int, ok: str = "", error: str = "") -> RedirectResponse:
    consulta = f"?ok={quote_plus(ok)}" if ok else (f"?error={quote_plus(error)}" if error else "")
    return RedirectResponse(f"/casos/{caso_id}{consulta}", status_code=303)


def _caso(db: Session, caso_id: int) -> Caso:
    caso = db.scalar(select(Caso).where(Caso.id == caso_id).options(selectinload(Caso.campaign)))
    if caso is None:
        raise HTTPException(404, "Ese caso no existe")
    return caso


def resumen(db: Session, ahora: datetime, pruebas: bool = False) -> dict:
    """Los números de arriba de la bandeja. «En plazo» mira los últimos 30 días."""
    base = select(func.count(Caso.id))
    if not pruebas:
        base = base.where(Caso.origen.not_in(recepcion.DE_PRUEBA))
    abiertos = db.scalar(base.where(Caso.estado == "abierto")) or 0
    vencidos = db.scalar(base.where(Caso.estado == "abierto", Caso.vence_at < ahora)) or 0
    desde = ahora - timedelta(days=30)
    respondidos = db.scalar(base.where(Caso.respondido_at >= desde)) or 0
    en_plazo = db.scalar(base.where(Caso.respondido_at >= desde, Caso.respondido_at <= Caso.vence_at)) or 0
    resueltos = db.scalar(base.where(Caso.estado == "resuelto", Caso.created_at >= desde)) or 0
    entraron = db.scalar(base.where(Caso.created_at >= desde, Caso.estado != "vacio")) or 0
    return {
        "abiertos": abiertos,
        "vencidos": vencidos,
        "en_plazo": round(100 * en_plazo / respondidos) if respondidos else None,
        "respondidos": respondidos,
        "resueltos": resueltos,
        "entraron": entraron,
    }


@router.get("/casos", response_class=HTMLResponse)
def bandeja(
    request: Request,
    filtro: str = "pendientes",
    area: str = "",
    agente: int | None = None,
    pruebas: int = 0,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    filtro = filtro if filtro in FILTROS else "pendientes"
    ahora = datetime.now(timezone.utc)
    consulta = select(Caso).options(selectinload(Caso.campaign)).where(Caso.estado != "vacio")
    if not pruebas:
        consulta = consulta.where(Caso.origen.not_in(recepcion.DE_PRUEBA))
    if agente:
        consulta = consulta.where(Caso.campaign_id == agente)
    if area == "-":
        consulta = consulta.where(Caso.area.is_(None))
    elif area:
        consulta = consulta.where(Caso.area == area)
    if filtro == "pendientes":
        consulta = consulta.where(or_(Caso.estado == "abierto", Caso.estado == "en_llamada"))
        # Lo urgente y lo que vence antes, arriba: es el orden en que hay que contestar.
        consulta = consulta.order_by(Caso.urgente.desc(), Caso.vence_at.asc().nulls_last(), Caso.id.desc())
    else:
        if filtro == "vencidos":
            consulta = consulta.where(Caso.estado == "abierto", Caso.vence_at < ahora)
        elif filtro == "respondidos":
            consulta = consulta.where(Caso.estado == "respondido")
        elif filtro == "resueltos":
            consulta = consulta.where(Caso.estado == "resuelto")
        elif filtro == "cerrados":
            consulta = consulta.where(Caso.estado == "cerrado")
        consulta = consulta.order_by(Caso.id.desc())
    casos = db.scalars(consulta.limit(MAX_LISTA)).all()

    agentes_recepcion = db.scalars(select(Campaign).where(Campaign.modo == "recepcion").order_by(Campaign.name)).all()
    areas = {a["clave"]: a["nombre"] for c in agentes_recepcion for a in recepcion.areas_de(c)}
    return templates.TemplateResponse(
        request, "casos.html",
        {
            "casos": casos, "filtro": filtro, "filtros": FILTROS, "area": area, "areas": areas,
            "agente": agente, "agentes_recepcion": agentes_recepcion, "pruebas": bool(pruebas),
            "resumen": resumen(db, ahora, bool(pruebas)), "ahora": ahora,
            "motivos": recepcion.MOTIVOS, "estados": recepcion.ESTADOS, "origenes": recepcion.ORIGENES,
            "vencido": recepcion.vencido, "plazo_restante": recepcion.plazo_restante,
        },
    )


@router.get("/casos/{caso_id}", response_class=HTMLResponse)
def detalle(request: Request, caso_id: int, ok: str | None = None, error: str | None = None,
            db: Session = Depends(get_db)) -> HTMLResponse:
    caso = _caso(db, caso_id)
    return templates.TemplateResponse(
        request, "caso.html",
        {
            "caso": caso, "ok": ok, "error": error, "ahora": datetime.now(timezone.utc),
            "areas": recepcion.areas_de(caso.campaign), "motivos": recepcion.MOTIVOS,
            "estados": recepcion.ESTADOS, "origenes": recepcion.ORIGENES, "vencido": recepcion.vencido,
            "plazo_restante": recepcion.plazo_restante, "de_prueba": caso.origen in recepcion.DE_PRUEBA,
        },
    )


@router.post("/casos/{caso_id}/responder")
def responder(request: Request, caso_id: int, respuesta: str = Form(""), db: Session = Depends(get_db)):
    caso = _caso(db, caso_id)
    if len(respuesta.strip()) < 3:
        return _volver(caso_id, error="Escribí qué se le respondió al cliente: queda en el recorrido del caso.")
    recepcion.responder(caso, respuesta, _usuario(request))
    db.commit()
    return _volver(caso_id, ok="Caso marcado como respondido.")


@router.post("/casos/{caso_id}/cerrar")
def cerrar(request: Request, caso_id: int, texto: str = Form(""), db: Session = Depends(get_db)):
    caso = _caso(db, caso_id)
    recepcion.cerrar(caso, _usuario(request), texto[:600])
    db.commit()
    return _volver(caso_id, ok="Caso cerrado.")


@router.post("/casos/{caso_id}/reabrir")
def reabrir(request: Request, caso_id: int, texto: str = Form(""), db: Session = Depends(get_db)):
    caso = _caso(db, caso_id)
    recepcion.reabrir(caso, _usuario(request), texto[:600])
    db.commit()
    return _volver(caso_id, ok="Caso reabierto: el plazo vuelve a correr desde ahora.")


@router.post("/casos/{caso_id}/area")
def reasignar(request: Request, caso_id: int, area: str = Form(""), db: Session = Depends(get_db)):
    caso = _caso(db, caso_id)
    destino = recepcion.area_por_clave(caso.campaign, area)
    if destino is None:
        return _volver(caso_id, error="Elegí un área de la lista.")
    if destino["clave"] == caso.area:
        return _volver(caso_id, ok="El caso ya estaba en esa área.")
    recepcion.derivar(caso, destino, por=_usuario(request))
    db.commit()
    avisar_caso(caso)
    return _volver(caso_id, ok=f"Caso pasado a {destino['nombre']}. El plazo corre desde ahora.")


@router.post("/casos/{caso_id}/nota")
def nota(request: Request, caso_id: int, texto: str = Form(""), db: Session = Depends(get_db)):
    caso = _caso(db, caso_id)
    if not texto.strip():
        return _volver(caso_id, error="La nota está vacía.")
    recepcion.evento(caso, "nota", texto.strip()[:600], por=_usuario(request))
    db.commit()
    return _volver(caso_id, ok="Nota agregada.")


# ─────────────────────────────────────────────────────────── prueba desde la ficha del agente
def _agente_recepcion(db: Session, campaign_id: int) -> Campaign:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Ese agente no existe")
    if not recepcion.es_recepcion(campaign):
        raise HTTPException(409, "Este agente no es de recepción")
    return campaign


def _caso_de_prueba(db: Session, campaign: Campaign, caso_id: str, origen: str) -> tuple[Caso, bool]:
    """El caso de esta prueba, o uno nuevo con el saludo como primera línea. Devuelve (caso, es_nuevo)."""
    if caso_id.isdigit():
        caso = db.get(Caso, int(caso_id))
        if caso is not None and caso.campaign_id == campaign.id and caso.origen == origen:
            return caso, False
    caso = recepcion.nuevo(campaign, origen)
    recepcion.decir(caso, "agente", campaign.intro_script.replace("{nombre}", "").replace("  ", " ").strip())
    db.add(caso)
    db.flush()
    return caso, True


def _estado(caso: Caso) -> dict:
    return {
        "caso_id": caso.id,
        "estado": recepcion.ESTADOS.get(caso.estado, caso.estado),
        "area": caso.area_nombre,
        "responsable": caso.responsable,
        "plazo_horas": caso.plazo_horas if caso.estado == "abierto" else None,
        "motivo": recepcion.MOTIVOS.get(caso.motivo or ""),
        "urgente": bool(caso.urgente),
        "enlace": f"/casos/{caso.id}",
    }


def _permitir(request: Request) -> JSONResponse | None:
    if not llm.is_configured():
        return JSONResponse({"error": "La IA no está configurada en este servidor."}, 503)
    if not agentes.permitir_ia(_usuario(request)):
        return JSONResponse({"error": "Muchas pruebas en la última hora. Probá más tarde."}, 429)
    return None


@router.post("/campaigns/{campaign_id}/recepcion/chat")
async def chat(request: Request, campaign_id: int, caso_id: str = Form(""), texto: str = Form(""),
               db: Session = Depends(get_db)) -> JSONResponse:
    """El chat de prueba: escribís como cliente y contesta el agente, con las mismas reglas que por teléfono."""
    campaign = _agente_recepcion(db, campaign_id)
    caso, es_nuevo = _caso_de_prueba(db, campaign, caso_id, "chat")
    lineas: list[dict] = [caso.conversacion[0]] if es_nuevo else []
    texto = " ".join(texto.split())[:1500]
    fin = False
    if texto:
        if (error := _permitir(request)) is not None:
            db.rollback()
            return error
        r = await recepcion.turno(caso, texto, conocimiento=conocimiento_del_caso(db, caso, texto))
        lineas.append({"quien": "agente", "texto": r["decir"]})
        if r["accion"] == "terminar":
            recepcion.terminar_llamada(caso, "se despidió")
            fin = True
    db.commit()
    return JSONResponse({"lineas": lineas, "fin": fin, **_estado(caso)})


@router.post("/campaigns/{campaign_id}/recepcion/simular")
async def simular(request: Request, campaign_id: int, caso_id: str = Form(""), perfil: str = Form("repuesto"),
                  perfil_texto: str = Form(""), db: Session = Depends(get_db)) -> JSONResponse:
    """Una vuelta de la simulación: Claude hace de cliente con el perfil elegido y el agente le contesta.

    El navegador la llama una y otra vez hasta `fin`, así la conversación se ve a medida que pasa.
    """
    campaign = _agente_recepcion(db, campaign_id)
    if (error := _permitir(request)) is not None:
        return error
    caso, es_nuevo = _caso_de_prueba(db, campaign, caso_id, "simulacion")
    descripcion = " ".join(perfil_texto.split())[:600] or recepcion.PERFILES.get(perfil, recepcion.PERFILES["repuesto"])[1]
    if es_nuevo:
        recepcion.evento(caso, "simulación", descripcion)
    lineas: list[dict] = [caso.conversacion[0]] if es_nuevo else []

    dice = None
    if recepcion.vueltas_del_cliente(caso) < MAX_VUELTAS_SIMULACION:
        dice = await recepcion.cliente_simulado(caso, descripcion)
    if dice is None:
        recepcion.terminar_llamada(caso, "terminó la simulación")
        db.commit()
        return JSONResponse({"lineas": lineas, "fin": True, **_estado(caso)})

    lineas.append({"quien": "cliente", "texto": dice})
    r = await recepcion.turno(caso, dice, conocimiento=conocimiento_del_caso(db, caso, dice))
    lineas.append({"quien": "agente", "texto": r["decir"]})
    fin = r["accion"] == "terminar"
    if fin:
        recepcion.terminar_llamada(caso, "se despidió")
    db.commit()
    return JSONResponse({"lineas": lineas, "fin": fin, **_estado(caso)})
