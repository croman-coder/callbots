"""Conocimiento: las bases de conocimiento que usan los agentes para responder (ver app/conocimiento.py)."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import agentes, conocimiento
from app.db import get_db
from app.deps import require_admin
from app.models import Brain, BrainSource, Campaign
from app.plantillas import templates
from app.services import llm

log = logging.getLogger(__name__)

router = APIRouter(tags=["conocimiento"], dependencies=[Depends(require_admin)])

MAX_ARCHIVOS = 25


def _usuario(request: Request) -> str | None:
    principal = getattr(request.state, "principal", None)
    return principal.usuario if principal else None


def _volver(destino: str, ok: str = "", error: str = "") -> RedirectResponse:
    consulta = f"ok={quote_plus(ok)}" if ok else (f"error={quote_plus(error)}" if error else "")
    return RedirectResponse(f"{destino}{'?' + consulta if consulta else ''}", status_code=303)


def _base(db: Session, brain_id: int) -> Brain:
    brain = db.scalar(select(Brain).where(Brain.id == brain_id).options(selectinload(Brain.fuentes)))
    if brain is None:
        raise HTTPException(404, "Esa base no existe")
    return brain


def _agentes_por_base(db: Session) -> dict[int, list[str]]:
    usan: dict[int, list[str]] = {}
    for c in db.scalars(select(Campaign).order_by(Campaign.name)).all():
        for i in c.brain_ids or []:
            usan.setdefault(int(i), []).append(c.name)
    return usan


@router.get("/conocimiento", response_class=HTMLResponse)
def lista(request: Request, ok: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    bases = db.scalars(select(Brain).options(selectinload(Brain.fuentes)).order_by(Brain.nombre)).all()
    return templates.TemplateResponse(
        request, "conocimiento.html",
        {"bases": bases, "usan": _agentes_por_base(db), "ok": ok, "error": error},
    )


@router.post("/conocimiento")
def crear(nombre: str = Form(""), descripcion: str = Form(""), db: Session = Depends(get_db)) -> RedirectResponse:
    nombre = " ".join(nombre.split())[:80]
    if not nombre:
        return _volver("/conocimiento", error="Ponele un nombre a la base, por ejemplo «Sucursales y horarios».")
    brain = Brain(nombre=nombre, descripcion=" ".join(descripcion.split())[:300] or None)
    db.add(brain)
    db.commit()
    return _volver(f"/conocimiento/{brain.id}", ok="Base creada. Sumale fuentes: texto, archivos o páginas web.")


@router.get("/conocimiento/{brain_id}", response_class=HTMLResponse)
def ver(request: Request, brain_id: int, ok: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    brain = _base(db, brain_id)
    total = sum(len(f.contenido or "") for f in brain.fuentes)
    return templates.TemplateResponse(
        request, "conocimiento_base.html",
        {
            "brain": brain, "total": total, "max_base": conocimiento.MAX_BASE,
            "usan": _agentes_por_base(db).get(brain.id, []), "ok": ok, "error": error,
            "extensiones": ", ".join(sorted(e.lstrip(".").upper() for e in conocimiento.EXTENSIONES)),
            "max_archivos": MAX_ARCHIVOS,
        },
    )


@router.post("/conocimiento/{brain_id}/editar")
def editar(brain_id: int, nombre: str = Form(""), descripcion: str = Form(""), db: Session = Depends(get_db)):
    brain = _base(db, brain_id)
    nombre = " ".join(nombre.split())[:80]
    if not nombre:
        return _volver(f"/conocimiento/{brain_id}", error="La base necesita un nombre.")
    brain.nombre, brain.descripcion = nombre, " ".join(descripcion.split())[:300] or None
    db.commit()
    return _volver(f"/conocimiento/{brain_id}", ok="Cambios guardados.")


def _agregar(db: Session, brain: Brain, tipo: str, titulo: str, origen: str | None, contenido: str, usuario: str | None) -> None:
    conocimiento.validar_tamano(brain, len(contenido))
    db.add(BrainSource(brain_id=brain.id, tipo=tipo, titulo=titulo[:200], origen=(origen or None) and origen[:500],
                       contenido=contenido, created_by=usuario))
    db.flush()
    db.refresh(brain)


@router.post("/conocimiento/{brain_id}/texto")
def agregar_texto(request: Request, brain_id: int, titulo: str = Form(""), texto: str = Form(""), db: Session = Depends(get_db)):
    brain = _base(db, brain_id)
    titulo = " ".join(titulo.split())[:200] or "Texto"
    contenido = conocimiento._limpio(texto)[: conocimiento.MAX_FUENTE]
    if len(contenido) < 20:
        return _volver(f"/conocimiento/{brain_id}", error="Pegá el texto que el agente tiene que saber.")
    try:
        _agregar(db, brain, "texto", titulo, None, contenido, _usuario(request))
    except conocimiento.ConocimientoError as exc:
        return _volver(f"/conocimiento/{brain_id}", error=str(exc))
    db.commit()
    return _volver(f"/conocimiento/{brain_id}", ok=f"Se sumó «{titulo}».")


@router.post("/conocimiento/{brain_id}/archivos")
async def agregar_archivos(request: Request, brain_id: int, archivos: list[UploadFile] = File([]), db: Session = Depends(get_db)):
    brain = _base(db, brain_id)
    archivos = [a for a in archivos if a and a.filename][:MAX_ARCHIVOS]
    if not archivos:
        return _volver(f"/conocimiento/{brain_id}", error="Elegí uno o más archivos.")
    sumados, errores = [], []
    for archivo in archivos:
        datos = await archivo.read(conocimiento.MAX_ARCHIVO_BYTES + 1)
        try:
            texto = conocimiento.texto_de_archivo(archivo.filename, datos)
            _agregar(db, brain, "archivo", archivo.filename.rsplit(".", 1)[0], archivo.filename, texto, _usuario(request))
            sumados.append(archivo.filename)
        except conocimiento.ConocimientoError as exc:
            errores.append(str(exc))
    db.commit()
    if errores and not sumados:
        return _volver(f"/conocimiento/{brain_id}", error=" ".join(errores)[:600])
    aviso = f"Se {'sumó' if len(sumados) == 1 else 'sumaron'} {len(sumados)} {'archivo' if len(sumados) == 1 else 'archivos'}."
    if errores:
        aviso += " No se pudieron leer: " + " ".join(errores)
    return _volver(f"/conocimiento/{brain_id}", ok=aviso[:600])


@router.post("/conocimiento/{brain_id}/web")
async def agregar_web(request: Request, brain_id: int, url: str = Form(""), db: Session = Depends(get_db)):
    brain = _base(db, brain_id)
    if not agentes.permitir(f"conocimiento:{_usuario(request)}"):
        return _volver(f"/conocimiento/{brain_id}", error="Sumaste muchas páginas en la última hora. Probá más tarde.")
    try:
        titulo, texto = await conocimiento.texto_de_pagina(url)
        _agregar(db, brain, "web", titulo, url.strip(), texto, _usuario(request))
    except conocimiento.ConocimientoError as exc:
        return _volver(f"/conocimiento/{brain_id}", error=str(exc))
    db.commit()
    return _volver(f"/conocimiento/{brain_id}", ok=f"Se sumó la página «{titulo}».")


@router.post("/conocimiento/{brain_id}/fuentes/{fuente_id}/borrar")
def borrar_fuente(brain_id: int, fuente_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    fuente = db.get(BrainSource, fuente_id)
    if fuente is None or fuente.brain_id != brain_id:
        return _volver(f"/conocimiento/{brain_id}", error="Esa fuente ya no está.")
    titulo = fuente.titulo
    db.delete(fuente)
    db.commit()
    return _volver(f"/conocimiento/{brain_id}", ok=f"Se sacó «{titulo}».")


@router.post("/conocimiento/{brain_id}/borrar")
def borrar_base(brain_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    brain = _base(db, brain_id)
    nombre = brain.nombre
    for c in db.scalars(select(Campaign)).all():
        if c.brain_ids and brain_id in [int(i) for i in c.brain_ids]:
            c.brain_ids = [int(i) for i in c.brain_ids if int(i) != brain_id] or None
    db.delete(brain)
    db.commit()
    return _volver("/conocimiento", ok=f"Se borró la base «{nombre}». Los agentes que la usaban siguen sin ella.")


PROBAR = """Respondés como lo haría el agente telefónico de la empresa: en una o dos oraciones, en español, tratando \
de usted, sin markdown. Usá SOLO la información de abajo; si no está, decí exactamente que eso no está en la base \
y que un asesor lo contactaría."""


@router.post("/conocimiento/{brain_id}/probar")
async def probar(request: Request, brain_id: int, pregunta: str = Form(""), db: Session = Depends(get_db)) -> JSONResponse:
    """Una pregunta de prueba: qué contestaría el agente con esta base (y qué partes usó)."""
    brain = _base(db, brain_id)
    pregunta = " ".join(pregunta.split())[:300]
    if not pregunta:
        return JSONResponse({"error": "Escribí una pregunta."}, 400)
    if not llm.is_configured():
        return JSONResponse({"error": "La IA no está configurada en este servidor."}, 503)
    if not agentes.permitir_ia(_usuario(request) or "?"):
        return JSONResponse({"error": "Muchas pruebas en la última hora. Probá más tarde."}, 429)
    fuentes = [conocimiento.Fuente(f.titulo, f.contenido) for f in brain.fuentes if f.contenido]
    if not fuentes:
        return JSONResponse({"error": "La base todavía no tiene fuentes."}, 400)
    texto = conocimiento.contexto(fuentes, pregunta)
    respuesta = await llm.complete(f"{PROBAR}\n\n{conocimiento.bloque(texto)}", pregunta, max_tokens=250)
    if not respuesta:
        return JSONResponse({"error": "Claude no respondió. Probá de nuevo."}, 502)
    return JSONResponse({"respuesta": respuesta.strip(), "usado": len(texto), "total": sum(len(f.contenido) for f in fuentes)})
