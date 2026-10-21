"""Agenda del taller en el panel: los turnos de cada día, cargar uno a mano y configurar talleres y servicios.

La lógica (horarios libres, tiempos, reserva) está en app/agenda.py; acá solo se arma la pantalla.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from itertools import zip_longest
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import agenda, avisos
from app.db import get_db
from app.deps import require_admin
from app.models import TallerServicio, TallerSucursal, TallerTurno
from app.plantillas import templates

log = logging.getLogger(__name__)

router = APIRouter(tags=["agenda"], dependencies=[Depends(require_admin)])


def _usuario(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.usuario if principal else "?"


def _volver(destino: str, **consulta: str) -> RedirectResponse:
    limpio = {k: v for k, v in consulta.items() if v}
    return RedirectResponse(destino + ("?" + urlencode(limpio) if limpio else ""), status_code=303)


def _dia(texto: str | None) -> date:
    try:
        return date.fromisoformat(texto) if texto else agenda.local(agenda.ahora()).date()
    except ValueError:
        return agenda.local(agenda.ahora()).date()


@router.get("/agenda", response_class=HTMLResponse)
def ver(request: Request, fecha: str | None = None, sucursal: int | None = None, pruebas: int = 0,
        ok: str | None = None, error: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    dia = _dia(fecha)
    sucursales = db.scalars(select(TallerSucursal).order_by(TallerSucursal.id)).all()
    suc = next((s for s in sucursales if s.id == sucursal), None) or next((s for s in sucursales if s.activa), None)
    turnos = []
    if suc is not None:
        ini = datetime.combine(dia, datetime.min.time(), tzinfo=agenda.tz()).astimezone(timezone.utc)
        consulta = select(TallerTurno).where(
            TallerTurno.sucursal_id == suc.id, TallerTurno.inicio >= ini, TallerTurno.inicio < ini + timedelta(days=1)
        ).order_by(TallerTurno.inicio, TallerTurno.bahia)
        if not pruebas:
            consulta = consulta.where(TallerTurno.estado != "prueba")
        turnos = db.scalars(consulta).all()
    servicios = agenda.servicios_activos(db)
    generales = sorted((s for s in servicios if not s.marca and not s.modelo), key=lambda s: s.id)
    # Horarios libres del primer servicio general: una referencia rápida para quien atiende el teléfono.
    referencia = generales[0] if generales else None
    libres = agenda.libres(db, suc, referencia, dia) if (suc and referencia) else []
    ocupan = [t for t in turnos if t.estado in agenda.OCUPAN]
    return templates.TemplateResponse(
        request, "agenda.html",
        {
            "dia": dia, "anterior": dia - timedelta(days=1), "siguiente": dia + timedelta(days=1),
            "hoy": agenda.local(agenda.ahora()).date(), "dia_texto": agenda.dia_texto(dia),
            "abre": agenda.horario_del_dia(suc, dia) if suc else None,
            "sucursales": sucursales, "suc": suc, "turnos": turnos, "ocupan": len(ocupan), "servicios": servicios,
            "referencia": referencia, "libres": [agenda.hora_texto(t) for t, _ in libres],
            "estados": agenda.ESTADOS, "hora": agenda.hora_texto, "pruebas": bool(pruebas), "ok": ok, "error": error,
        },
    )


@router.post("/agenda/turnos")
def nuevo_turno(request: Request, sucursal_id: int = Form(...), servicio_id: int = Form(...), fecha: str = Form(...),
                hora: str = Form(...), cliente: str = Form(""), telefono: str = Form(""), marca: str = Form(""),
                modelo: str = Form(""), chapa: str = Form(""), km: str = Form(""), notas: str = Form(""),
                db: Session = Depends(get_db)) -> RedirectResponse:
    destino = "/agenda"
    vuelta = {"fecha": fecha, "sucursal": str(sucursal_id)}
    suc, serv = db.get(TallerSucursal, sucursal_id), db.get(TallerServicio, servicio_id)
    if suc is None or serv is None:
        return _volver(destino, **vuelta, error="Elegí el taller y el servicio.")
    if not cliente.strip() or not telefono.strip():
        return _volver(destino, **vuelta, error="Faltan el nombre y el teléfono del cliente.")
    momento = agenda._hm(hora)
    if momento is None:
        return _volver(destino, **vuelta, error="La hora va como HH:MM, por ejemplo 08:00.")
    inicio = datetime.combine(_dia(fecha), momento, tzinfo=agenda.tz())
    try:
        turno = agenda.reservar(
            db, suc=suc, serv=serv, inicio=inicio, cliente=cliente.strip(), telefono=telefono.strip(),
            marca=marca.strip() or None, modelo=modelo.strip() or None, chapa=chapa.strip().upper() or None,
            km=int("".join(c for c in km if c.isdigit()) or 0) or None, notas=notas.strip()[:1000] or None,
            origen="panel", por=_usuario(request),
        )
    except agenda.HorarioNoDisponible as exc:
        db.rollback()
        return _volver(destino, **vuelta, error=str(exc))
    db.commit()
    avisos.por_turno_reservado(turno.id)
    return _volver(destino, **vuelta, ok=f"Turno de las {agenda.hora_texto(turno.inicio)} reservado en la bahía {turno.bahia}.")


@router.post("/agenda/turnos/{turno_id}/estado")
def estado(request: Request, turno_id: int, estado: str = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    turno = db.scalar(select(TallerTurno).where(TallerTurno.id == turno_id).options(selectinload(TallerTurno.sucursal)))
    if turno is None:
        raise HTTPException(404, "Ese turno no existe")
    vuelta = {"fecha": agenda.local(turno.inicio).date().isoformat(), "sucursal": str(turno.sucursal_id)}
    try:
        agenda.cambiar_estado(turno, estado, _usuario(request))
    except ValueError:
        return _volver("/agenda", **vuelta, error="Elegí un estado de la lista.")
    db.commit()
    if estado == "listo":
        avisos.por_vehiculo_listo(turno.id)
    return _volver("/agenda", **vuelta, ok=f"Turno de {turno.cliente_nombre or 'sin nombre'}: {agenda.ESTADOS[estado].lower()}.")


# ─────────────────────────────────────────────────────────── talleres y servicios
@router.get("/agenda/talleres", response_class=HTMLResponse)
def talleres(request: Request, ok: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "agenda_talleres.html",
        {
            "sucursales": db.scalars(select(TallerSucursal).order_by(TallerSucursal.id)).all(),
            "servicios": db.scalars(select(TallerServicio).order_by(TallerServicio.nombre, TallerServicio.marca,
                                                                     TallerServicio.modelo)).all(),
            "dias": agenda.DIAS, "ok": ok, "error": error,
        },
    )


def _entero(valor: str, minimo: int, maximo: int, defecto: int) -> int:
    try:
        return min(max(int(valor), minimo), maximo)
    except (TypeError, ValueError):
        return defecto


@router.post("/agenda/talleres")
async def guardar_taller(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    sid = str(form.get("id") or "")
    suc = db.get(TallerSucursal, int(sid)) if sid.isdigit() else TallerSucursal()
    if suc is None:
        raise HTTPException(404, "Ese taller no existe")
    nombre = " ".join(str(form.get("nombre") or "").split())[:80]
    if not nombre:
        return _volver("/agenda/talleres", error="El taller necesita un nombre.")
    suc.nombre = nombre
    suc.direccion = " ".join(str(form.get("direccion") or "").split())[:200] or None
    suc.activa = "activa" in form
    suc.horario = agenda.normalizar_horario(
        {d: (str(form.get(f"desde_{d}") or ""), str(form.get(f"hasta_{d}") or "")) for d in range(7)}
    )
    suc.intervalo_min = _entero(form.get("intervalo_min"), 10, 240, 30)
    suc.bahias = _entero(form.get("bahias"), 1, 50, 2)
    suc.recepciones_por_franja = _entero(form.get("recepciones_por_franja"), 1, 20, 2)
    suc.margen_retiro_min = _entero(form.get("margen_retiro_min"), 0, 600, 60)
    suc.anticipacion_min = _entero(form.get("anticipacion_min"), 0, 2880, 120)
    if suc.activa and not suc.horario:
        return _volver("/agenda/talleres", error=f"{nombre}: cargá al menos un día con horario, o desactivalo.")
    if suc.id is None:
        db.add(suc)
    db.commit()
    return _volver("/agenda/talleres", ok=f"Taller «{nombre}» guardado.")


@router.post("/agenda/servicios")
async def guardar_servicios(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Todos los servicios en un formulario: una fila por servicio (listas en paralelo). Sin nombre, se desactiva."""
    form = await request.form()
    existentes = {s.id: s for s in db.scalars(select(TallerServicio)).all()}
    filas = zip_longest(*(form.getlist(c) for c in ("sv_id", "sv_nombre", "sv_palabras", "sv_marca", "sv_modelo",
                                                    "sv_recepcion", "sv_trabajo", "sv_lavado", "sv_calidad",
                                                    "sv_entrega")), fillvalue="")
    for sid, nombre, palabras, marca, modelo, rec, trab, lav, cal, ent in filas:
        nombre = " ".join(str(nombre).split())[:120]
        serv = existentes.get(int(sid)) if str(sid).isdigit() else None
        if not nombre:
            if serv is not None:
                serv.activo = False  # no se borra: los turnos viejos lo nombran
            continue
        if serv is None:
            serv = TallerServicio()
            db.add(serv)
        serv.nombre, serv.activo = nombre, True
        serv.palabras = " ".join(str(palabras).split())[:300] or None
        serv.marca = " ".join(str(marca).split())[:40] or None
        serv.modelo = " ".join(str(modelo).split())[:60] or None
        serv.recepcion_min = _entero(rec, 0, 240, 5)
        serv.trabajo_min = _entero(trab, 5, 1440, 60)
        serv.lavado_min = _entero(lav, 0, 240, 0)
        serv.calidad_min = _entero(cal, 0, 240, 5)
        serv.entrega_min = _entero(ent, 0, 240, 10)
    db.commit()
    return _volver("/agenda/talleres", ok="Servicios guardados.")
