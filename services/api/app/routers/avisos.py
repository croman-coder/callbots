"""Avisos por WhatsApp en el panel (qué se avisa, con qué plantilla, prueba y registro) y el webhook de YCloud.

La lógica está en app/avisos.py.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import avisos
from app.config import settings
from app.db import get_db
from app.deps import require_admin
from app.models import WhatsappEnvio
from app.plantillas import templates

log = logging.getLogger(__name__)

router = APIRouter(tags=["avisos"], dependencies=[Depends(require_admin)])
webhook_router = APIRouter(tags=["avisos"])  # lo llama YCloud: sin sesión, con firma


def _usuario(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.usuario if principal else "?"


def _volver(**consulta: str) -> RedirectResponse:
    limpio = {k: v for k, v in consulta.items() if v}
    return RedirectResponse("/avisos" + ("?" + urlencode(limpio) if limpio else ""), status_code=303)


@router.get("/avisos", response_class=HTMLResponse)
def ver(request: Request, ok: str | None = None, error: str | None = None, db: Session = Depends(get_db)):
    cfg = avisos.todas(db)
    db.commit()  # crea las filas que falten, apagadas
    meta, error_meta = {}, None
    if avisos.configurado() and settings.ycloud_waba_id.strip():
        try:
            meta = avisos.plantillas_en_meta()
        except avisos.AvisoError as exc:
            error_meta = str(exc)
    envios = db.scalars(select(WhatsappEnvio).order_by(WhatsappEnvio.id.desc()).limit(40)).all()
    return templates.TemplateResponse(
        request, "avisos.html",
        {
            "eventos": avisos.EVENTOS, "cfg": cfg, "meta": meta, "error_meta": error_meta,
            "configurado": avisos.configurado(), "numero": avisos.e164(settings.ycloud_numero),
            "hay_clave": bool(settings.ycloud_api_key.strip()), "hay_waba": bool(settings.ycloud_waba_id.strip()),
            "hay_secreto": bool(settings.ycloud_webhook_secreto.strip()),
            "webhook_url": f"{settings.panel_url.rstrip('/')}/webhooks/ycloud",
            "envios": envios, "estados": avisos.ESTADOS, "texto_con": avisos.texto_con, "ok": ok, "error": error,
        },
    )


@router.post("/avisos/config")
async def guardar(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    form = await request.form()
    for evento in avisos.EVENTOS:
        fila = avisos.config(db, evento)
        fila.activo = f"activo_{evento}" in form
        plantilla = str(form.get(f"plantilla_{evento}") or "").strip().lower()[:120]
        fila.plantilla = plantilla or avisos.EVENTOS[evento].plantilla
        fila.idioma = (str(form.get(f"idioma_{evento}") or "es").strip() or "es")[:10]
        if avisos.EVENTOS[evento].hora is not None:
            hora = str(form.get(f"hora_{evento}") or "").strip()
            fila.hora = hora if len(hora) == 5 and hora[2] == ":" else avisos.EVENTOS[evento].hora
        fila.actualizado_por = _usuario(request)
    db.commit()
    return _volver(ok="Avisos guardados.")


@router.post("/avisos/plantillas")
def pedir_aprobacion(evento: str = Form(...)) -> RedirectResponse:
    """Le manda a Meta la plantilla sugerida de un evento para que la apruebe (lo pide quien toca el botón)."""
    if evento not in avisos.EVENTOS:
        return _volver(error="Ese aviso no existe.")
    try:
        avisos.pedir_aprobacion(evento)
    except avisos.AvisoError as exc:
        return _volver(error=f"No se pudo pedir la aprobación: {exc}")
    return _volver(ok=f"Plantilla «{avisos.EVENTOS[evento].plantilla}» enviada a Meta. La aprobación suele tardar "
                      "de minutos a un día; el estado se ve acá.")


@router.post("/avisos/prueba")
def probar(evento: str = Form(...), telefono: str = Form(""), db: Session = Depends(get_db)) -> RedirectResponse:
    """Manda el aviso con los datos de ejemplo a un número que escribe quien prueba (aunque el aviso esté apagado)."""
    if evento not in avisos.EVENTOS:
        return _volver(error="Ese aviso no existe.")
    if avisos.e164(telefono) is None:
        return _volver(error="Escribí un celular válido, por ejemplo 0981 123 456.")
    envio = avisos.registrar(db, evento, telefono=telefono, nombre="Prueba",
                             variables=list(avisos.EVENTOS[evento].ejemplo), prueba=True)
    db.commit()
    if envio.estado != "pendiente":
        return _volver(error=f"No se mandó: {avisos.ESTADOS[envio.estado].lower()}.")
    avisos.despachar(envio.id)
    return _volver(ok="Prueba en camino. El estado (enviado, entregado, leído o falló) aparece abajo en un momento.")


@webhook_router.post("/webhooks/ycloud")
async def webhook(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Estados de los mensajes (enviado, entregado, leído, falló). Sin firma válida se rechaza."""
    cuerpo = await request.body()
    if not avisos.firma_valida(request.headers.get("YCloud-Signature"), cuerpo, settings.ycloud_webhook_secreto):
        return JSONResponse({"error": "firma inválida"}, 401)
    try:
        evento = json.loads(cuerpo)
    except ValueError:
        return JSONResponse({"error": "JSON inválido"}, 400)
    if avisos.aplicar_estado(db, evento if isinstance(evento, dict) else {}):
        db.commit()
    return JSONResponse({"ok": True})
