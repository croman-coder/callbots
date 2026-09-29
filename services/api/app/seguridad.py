"""Cabeceras de seguridad para todas las respuestas.

Middleware ASGI puro y no `@app.middleware("http")`: ese envuelve la respuesta
en un stream propio, y con el audio de vista previa de la voz y los WebSockets
del simulador ya dio problemas de buffering en otros proyectos.
"""

from __future__ import annotations

import re

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

# Origen completo, sin comodines ni rutas. Lo que no encaje se descarta: el valor
# va a un encabezado, y un salto de línea o un `;` colado permitiría inyectar
# directivas nuevas.
_ORIGEN = re.compile(r"^https?://[A-Za-z0-9.-]+(:\d{1,5})?$")


def _tokens() -> list[str]:
    return [t for t in re.split(r"[\s,]+", settings.frame_ancestors) if t]


def frame_ancestors() -> str:
    return " ".join(["'self'", *[t for t in _tokens() if _ORIGEN.match(t)]])


def origenes_descartados() -> list[str]:
    """Lo que hay en FRAME_ANCESTORS y no se aplicó. Descartar es lo seguro, pero
    en silencio deja el iframe del CRM roto sin que nada diga por qué."""
    return [t for t in _tokens() if not _ORIGEN.match(t)]


class CabecerasSeguras:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        ruta = scope.get("path", "")

        async def enviar(mensaje: Message) -> None:
            if mensaje["type"] == "http.response.start":
                h = MutableHeaders(scope=mensaje)
                # Quién puede enmarcar el panel. Reemplaza a X-Frame-Options, que
                # no sabe expresar "solo este origen".
                h.setdefault("Content-Security-Policy", f"frame-ancestors {frame_ancestors()}")
                h.setdefault("X-Content-Type-Options", "nosniff")
                h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                if ruta.startswith("/static/"):
                    h.setdefault("Cache-Control", "public, max-age=604800")
            await send(mensaje)

        await self.app(scope, receive, enviar)
