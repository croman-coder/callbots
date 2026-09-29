"""Entrada al panel desde el CRM: canjea el ticket por una sesión.

Único endpoint sin autenticación del panel, porque es el que la crea. Todo lo
demás vive detrás de `require_admin`. Ver app/sso.py para el diseño.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app import sso

log = logging.getLogger(__name__)

router = APIRouter(tags=["acceso"])

# Página mínima y autocontenida: se muestra dentro de un iframe, donde no hay
# barra de navegación a la que volver ni se puede depender de la hoja de estilos.
def pagina_aviso(titulo: str, detalle: str) -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{titulo}</title>
<style>
  body{{margin:0;min-height:100dvh;display:grid;place-items:center;background:#111936;color:#F1F5F9;
       font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;padding:24px;text-align:center}}
  main{{max-width:420px}} h1{{font-size:19px;margin:0 0 8px;text-wrap:balance}}
  p{{margin:0;color:#c4cce6}}
</style></head><body><main><h1>{titulo}</h1><p>{detalle}</p></main></body></html>"""


def destino_seguro(destino: str | None) -> str:
    """Solo rutas de este mismo sitio. Un `next` libre sería un redirect abierto:
    alguien podría armar un enlace legítimo de Callbot que termine en otro sitio."""
    if not destino or any(c in destino for c in "\r\n\\"):
        return "/"
    partes = urlsplit(destino)
    if partes.scheme or partes.netloc:
        return "/"
    ruta = partes.path or "/"
    if not ruta.startswith("/") or ruta.startswith("//") or ruta.startswith("/sso"):
        return "/"
    return ruta + (f"?{partes.query}" if partes.query else "")


@router.get("/sso", include_in_schema=False)
def canjear(ticket: str = "", next: str = "/") -> Response:  # noqa: A002 - nombre público del parámetro
    if not sso.habilitado():
        # Sin secreto la ruta no existe: no se anuncia que hay un acceso apagado.
        raise HTTPException(status_code=404)

    principal = sso.canjear_ticket(ticket)
    if principal is None:
        log.warning("SSO: ticket rechazado (inválido, vencido, reusado o de un rol sin acceso)")
        return HTMLResponse(
            pagina_aviso(
                "No se pudo abrir Calidad",
                "El acceso venció o no está autorizado. Volvé a abrir la pestaña Calidad desde el CRM.",
            ),
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )

    respuesta = RedirectResponse(destino_seguro(next), status_code=303)
    respuesta.set_cookie(
        sso.COOKIE,
        sso.emitir_sesion(principal),
        max_age=sso.TTL_SESION,
        httponly=True,   # ningún script de la página puede leerla
        secure=True,     # nunca por http plano
        samesite="lax",  # crm.* y callbot.* son el mismo sitio: viaja en el iframe, no en un POST ajeno
        path="/",
    )
    respuesta.headers["Cache-Control"] = "no-store"
    log.info("SSO: sesión abierta para %s (%s)", principal.usuario, principal.rol)
    return respuesta
