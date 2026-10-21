"""Pantalla Usuarios: administrar las cuentas de los dashboards desde Callbot.

Solo la ve quien entra como administrador con su cuenta de los dashboards (super admin). Ver app/usuarios.py para el
diseño: el padrón y las reglas son los de los dashboards, Callbot solo les presta una pantalla.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import cuentas, usuarios
from app.deps import require_admin
from app.plantillas import templates

log = logging.getLogger(__name__)

router = APIRouter(tags=["usuarios"], dependencies=[Depends(require_admin)])

_CORREO = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def _quien(request: Request):
    """La persona que administra. Un administrador por la credencial de operación (sin correo) no puede: no tiene
    una cuenta de los dashboards con la que hablarles."""
    principal = getattr(request.state, "principal", None)
    if principal is None or principal.rol != "admin":
        raise HTTPException(403, "Solo quien administra puede ver los usuarios.")
    return principal


def _pagina(request: Request, *, modo: str, gente: list | None = None, aviso: str = "", error: str = "", status: int = 200) -> Response:
    principal = _quien(request)
    return templates.TemplateResponse(
        request,
        "usuarios.html",
        {
            "modo": modo,
            "gente": gente or [],
            "ok": aviso,
            "error": error,
            "yo": principal.usuario,
            "roles": usuarios.ROLES,
            "marcas": usuarios.MARCAS,
            "min_clave": usuarios.MIN_CLAVE,
            "dominio": (cuentas.settings.login_dominios.split(",")[0].strip() or "santarosa.com.py"),
        },
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/usuarios", response_class=HTMLResponse)
def ver(request: Request, ok: str | None = None, error: str | None = None) -> Response:
    principal = _quien(request)
    if not cuentas.habilitado():
        return _pagina(request, modo="apagado")
    if not _CORREO.fullmatch(principal.usuario or ""):
        return _pagina(request, modo="sin_cuenta")
    token = request.cookies.get(usuarios.COOKIE_TOKEN)
    if not token:
        return _pagina(request, modo="confirmar", aviso=ok or "", error=error or "")
    try:
        gente = usuarios.listar(token)
    except usuarios.UsuariosError as exc:
        respuesta = _pagina(request, modo="confirmar" if exc.reautenticar else "error", error=exc.motivo, status=exc.estado if not exc.reautenticar else 200)
        if exc.reautenticar:
            usuarios.borrar_cookie(respuesta)
        return respuesta
    return _pagina(request, modo="lista", gente=gente, aviso=ok or "", error=error or "")


@router.post("/usuarios/acceso", include_in_schema=False)
def confirmar(request: Request, clave: str = Form("")) -> Response:
    """Confirma la clave de quien administra y guarda su token de Supabase, unos minutos y solo para /usuarios."""
    principal = _quien(request)
    if not cuentas.habilitado() or not _CORREO.fullmatch(principal.usuario or ""):
        raise HTTPException(404)
    origen = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
    try:
        quien, token, vence = cuentas.iniciar_sesion_con_token(principal.usuario, clave, origen=origen)
    except cuentas.LoginRechazado as e:
        return _pagina(request, modo="confirmar", error=e.motivo, status=503 if e.transitorio else 401)
    if quien.rol != "admin":
        return _pagina(request, modo="confirmar", error="Tu cuenta no es super admin en los dashboards.", status=403)
    respuesta = RedirectResponse(usuarios.RUTA, status_code=303)
    usuarios.poner_cookie(respuesta, token, vence)
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


@router.post("/usuarios/api", include_in_schema=False)
async def accion(request: Request) -> Response:
    principal = _quien(request)
    token = request.cookies.get(usuarios.COOKIE_TOKEN)
    if not token:
        return JSONResponse({"error": "Confirmá tu clave para administrar usuarios.", "reautenticar": True}, 401)
    try:
        datos = await request.json()
        if not isinstance(datos, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"error": "El pedido no se entendió."}, 400)
    try:
        aviso = usuarios.ejecutar(token, principal.usuario, datos)
    except usuarios.UsuariosError as exc:
        respuesta = JSONResponse({"error": exc.motivo, "reautenticar": exc.reautenticar}, exc.estado)
        if exc.reautenticar:
            usuarios.borrar_cookie(respuesta)
        return respuesta
    return JSONResponse({"ok": aviso}, headers={"Cache-Control": "no-store"})
