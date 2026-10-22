"""Locutor a distancia: la página pública donde la persona graba (con su enlace) y la revisión en Voces.

La lógica está en app/locutores.py. La página pública no pide sesión: la llave es el enlace, de un solo uso y con
vencimiento. Solo acepta las grabaciones que pide (WAV cortos) y nada más.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agentes, locutores
from app.db import get_db
from app.deps import require_admin
from app.models import GrabacionVoz
from app.plantillas import templates
from app.services import voicestudio

log = logging.getLogger(__name__)

router = APIRouter(tags=["voces"], dependencies=[Depends(require_admin)])
publico = APIRouter(tags=["voces"])


def _usuario(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    return principal.usuario if principal else "?"


def _volver(ok: str = "", error: str = "") -> RedirectResponse:
    consulta = f"?ok={quote_plus(ok)}" if ok else (f"?error={quote_plus(error)}" if error else "")
    return RedirectResponse(f"/voices{consulta}#locutores", status_code=303)


def _por_token(db: Session, token: str) -> GrabacionVoz | None:
    if not token or len(token) > 64:
        return None
    return db.scalar(select(GrabacionVoz).where(GrabacionVoz.token == token))


# ─────────────────────────────────────────────────────────── página pública
@publico.get("/grabar/{token}", response_class=HTMLResponse)
def pagina(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    g = _por_token(db, token)
    vigente = g is not None and locutores.vigente(g)
    return templates.TemplateResponse(
        request, "grabar.html",
        {
            "g": g if vigente else None,
            "motivo": None if vigente else ("Este enlace no existe." if g is None else
                                            "Ya recibimos tus grabaciones. Gracias." if g.estado != "esperando" else
                                            "Este enlace venció. Pedí uno nuevo a quien te lo mandó."),
            "frases": locutores.frases(g.genero) if vigente else [],
            "permiso": locutores.voces.frase_consentimiento(g.persona) if vigente else "",
            "max_toma": int(locutores.MAX_TOMA_SEGUNDOS), "max_permiso": int(locutores.MAX_PERMISO_SEGUNDOS),
        },
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
    )


@publico.post("/grabar/{token}")
async def recibir(request: Request, token: str, acepto: str = Form(""), consentimiento: UploadFile | None = File(None),
                  toma_1: UploadFile | None = File(None), toma_2: UploadFile | None = File(None),
                  toma_3: UploadFile | None = File(None), toma_4: UploadFile | None = File(None),
                  toma_5: UploadFile | None = File(None), db: Session = Depends(get_db)) -> JSONResponse:
    g = _por_token(db, token)
    if g is None:
        return JSONResponse({"error": "Este enlace no existe."}, 404)
    if acepto != "si":
        return JSONResponse({"error": "Para seguir tenés que aceptar el uso de tu voz."}, 400)
    if not agentes.permitir(f"grabar:{token}"):
        return JSONResponse({"error": "Muchos intentos seguidos. Esperá un rato y probá de nuevo."}, 429)

    async def leer(archivo: UploadFile | None) -> bytes | None:
        return await archivo.read(locutores.MAX_BYTES + 1) if archivo is not None else None

    try:
        locutores.recibir(g, await leer(consentimiento),
                          [await leer(t) for t in (toma_1, toma_2, toma_3, toma_4, toma_5)])
    except locutores.GrabacionInvalida as exc:
        db.rollback()
        return JSONResponse({"error": str(exc)}, 400)
    db.commit()
    log.info("Grabación de locutor %s recibida (%s)", g.id, g.persona)
    return JSONResponse({"ok": True})


# ─────────────────────────────────────────────────────────── revisión en Voces
@router.post("/voices/locutores")
def crear(request: Request, persona: str = Form(""), genero: str = Form(""), nota: str = Form(""),
          db: Session = Depends(get_db)) -> RedirectResponse:
    try:
        g = locutores.nuevo(db, persona, genero, nota, _usuario(request))
    except locutores.GrabacionInvalida as exc:
        return _volver(error=str(exc))
    db.commit()
    return _volver(ok=f"Enlace listo para {g.persona}. Copialo y mandáselo: vence en {locutores.DIAS_VIGENCIA} días.")


@router.get("/voices/locutores/{grabacion_id}/audio/{archivo}")
def escuchar(grabacion_id: int, archivo: str, db: Session = Depends(get_db)) -> Response:
    g = db.get(GrabacionVoz, grabacion_id)
    datos = locutores.audio(g, archivo) if g is not None else None
    if datos is None:
        raise HTTPException(404, "Ese audio no existe")
    return Response(datos, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.post("/voices/locutores/{grabacion_id}/aprobar")
async def aprobar(request: Request, grabacion_id: int, toma: int = Form(-1), nombre: str = Form(""),
                  db: Session = Depends(get_db)) -> RedirectResponse:
    g = db.get(GrabacionVoz, grabacion_id)
    if g is None:
        return _volver(error="Esa grabación ya no existe.")
    if not voicestudio.is_configured():
        return _volver(error="El motor de voces clonadas no está instalado en el servidor.")
    try:
        # httpx sincrónico y la placa de video codificando la grabación: a un hilo, que la API siga atendiendo.
        voz = await asyncio.to_thread(locutores.aprobar, g, toma - 1, nombre, _usuario(request))
    except locutores.GrabacionInvalida as exc:
        return _volver(error=str(exc))
    except voicestudio.VoiceStudioError as exc:
        return _volver(error=f"No se pudo clonar: {exc}")
    db.commit()
    log.info("Voz de locutor %s aprobada: %s", g.id, voz)
    return _volver(ok=f"Voz de {g.persona} lista. Escuchala en la biblioteca y elegila en la ficha de un agente.")


@router.post("/voices/locutores/{grabacion_id}/rechazar")
def rechazar(request: Request, grabacion_id: int, motivo: str = Form(""), db: Session = Depends(get_db)):
    g = db.get(GrabacionVoz, grabacion_id)
    if g is None:
        return _volver(error="Esa grabación ya no existe.")
    locutores.rechazar(g, motivo, _usuario(request))
    db.commit()
    return _volver(ok=f"Grabación de {g.persona} rechazada; se borraron sus audios.")


@router.post("/voices/locutores/{grabacion_id}/borrar")
def borrar(grabacion_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    g = db.get(GrabacionVoz, grabacion_id)
    if g is None:
        return _volver(error="Esa grabación ya no existe.")
    persona = g.persona
    locutores.borrar_archivos(g)
    db.delete(g)
    db.commit()
    return _volver(ok=f"Se borró el enlace de {persona} y sus audios.")
