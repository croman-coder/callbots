"""Voces → Pronunciación: cómo tiene que decir el bot las marcas y siglas (ver app/pronunciaciones.py)."""

from __future__ import annotations

import logging
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import pronunciaciones
from app.db import get_db
from app.deps import require_admin
from app.models import Pronunciation

log = logging.getLogger(__name__)

router = APIRouter(tags=["pronunciacion"], dependencies=[Depends(require_admin)])


def _volver(ok: str = "", error: str = "") -> RedirectResponse:
    consulta = f"ok={quote_plus(ok)}" if ok else f"error={quote_plus(error)}"
    return RedirectResponse(f"/voices?{consulta}#pronunciacion", status_code=303)


@router.post("/pronunciaciones")
def guardar(palabra: str = Form(""), dicho: str = Form(""), db: Session = Depends(get_db)) -> RedirectResponse:
    """Agrega una palabra o, si ya está, cambia cómo se dice."""
    try:
        palabra, dicho = pronunciaciones.validar(palabra, dicho)
    except pronunciaciones.PronunciacionError as exc:
        return _volver(error=str(exc))
    existente = db.scalar(select(Pronunciation).where(func.lower(Pronunciation.palabra) == palabra.lower()))
    if existente is not None:
        existente.dicho = dicho
        aviso = f"«{existente.palabra}» ahora se dice «{dicho}»."
    else:
        if (db.scalar(select(func.count()).select_from(Pronunciation)) or 0) >= pronunciaciones.MAX_ENTRADAS:
            return _volver(error=f"Hay {pronunciaciones.MAX_ENTRADAS} palabras: borrá alguna antes de sumar otra.")
        db.add(Pronunciation(palabra=palabra, dicho=dicho))
        aviso = f"«{palabra}» se va a decir «{dicho}»."
    db.commit()
    return _volver(ok=aviso + " Vale para las próximas frases que genere el bot.")


@router.post("/pronunciaciones/{entrada_id}/borrar")
def borrar(entrada_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    fila = db.get(Pronunciation, entrada_id)
    if fila is None:
        return _volver(error="Esa palabra ya no está.")
    palabra = fila.palabra
    db.delete(fila)
    db.commit()
    return _volver(ok=f"«{palabra}» vuelve a decirse como se escribe.")
