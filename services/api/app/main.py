"""Punto de entrada de la API + panel de administración."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import cuentas, log_redaction, sso
from app.config import settings
from app.deps import IrALogin, SesionRequerida
from app.routers import acceso, admin, agente, internal, simulator, usuarios
from app.seguridad import CabecerasSeguras, frame_ancestors, origenes_descartados

# Las tareas se declaran con @shared_task, que resuelve el broker recién al
# encolar, mirando la app de Celery "actual" del proceso. El worker arranca con
# -A app.scheduler.worker y la tiene; la API no importaba ese módulo por ningún
# lado, así que cada .delay() caía en la app default de Celery —sin broker— y
# terminaba en "Connection refused" contra localhost. Importarlo la registra
# para todo el proceso.
from app.scheduler import worker as _celery_worker  # noqa: F401

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log_redaction.instalar()
log = logging.getLogger(__name__)

app = FastAPI(
    title="Callbot - Encuestas de satisfacción",
    description=(
        "Agente de voz que llama al cliente 48hs después del ingreso al taller, "
        "hace la encuesta y devuelve el resultado a Bitrix24."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(CabecerasSeguras)

# Tipografía y demás recursos propios. Públicos a propósito: son fuentes, y tienen
# que cargar también dentro del iframe del CRM, donde no hay Basic que valga.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(acceso.router)
app.include_router(internal.router)
app.include_router(agente.router)
app.include_router(agente.ws_router)
app.include_router(admin.router)
app.include_router(simulator.router)
app.include_router(usuarios.router)


@app.exception_handler(SesionRequerida)
async def sesion_requerida(request: Request, exc: SesionRequerida) -> HTMLResponse:
    return HTMLResponse(
        acceso.pagina_aviso(
            "La sesión venció",
            "Volvé a abrir la pestaña Calidad desde el CRM para seguir.",
        ),
        status_code=401,
        headers={"Cache-Control": "no-store"},
    )


@app.exception_handler(IrALogin)
async def ir_a_login(request: Request, exc: IrALogin) -> RedirectResponse:
    return RedirectResponse(
        "/login?" + urlencode({"next": exc.destino}), status_code=303, headers={"Cache-Control": "no-store"}
    )


@app.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    """Liveness para el healthcheck de Docker. Sin dependencias externas.

    El chequeo profundo (Bitrix, Asterisk, Claude, Voicebox) está en /health-detail, que
    requiere autenticación porque expone detalles de infraestructura.
    """
    return JSONResponse({"status": "ok"})


@app.on_event("startup")
def on_startup() -> None:
    log.info("Callbot API arriba | zona=%s | demora=%sh", settings.tz, settings.survey_delay_hours)

    if not settings.bitrix_webhook_url.startswith("http"):
        log.warning("BITRIX_WEBHOOK_URL no configurado: la sincronización va a fallar")
    if settings.internal_token == "dev-internal-token":
        log.warning("INTERNAL_TOKEN tiene el valor por default: cambialo en producción")
    descartados = origenes_descartados()
    if descartados:
        log.warning(
            "FRAME_ANCESTORS: se ignoró %s (no es un origen https://host[:puerto] exacto, "
            "sin ';' ni comodines). Si era el CRM, el iframe no va a cargar.", descartados,
        )
    if sso.habilitado():
        log.info("Acceso desde el CRM activo | frame-ancestors=%s", frame_ancestors())
    elif settings.sso_secret:
        log.warning("CALLBOT_SSO_SECRET tiene menos de 32 caracteres: el acceso desde el CRM queda DESACTIVADO")
    if cuentas.habilitado() and sso.habilitado():
        log.info("Login con cuenta de los dashboards activo | roles=%s", settings.login_roles)
    elif cuentas.habilitado():
        log.warning("SUPABASE_URL está puesta pero CALLBOT_SSO_SECRET no (>=32): el login queda DESACTIVADO")
    if settings.admin_password in ("admin", "cambiar_esta_password"):
        log.warning("ADMIN_PASSWORD tiene el valor por default: cambialo en producción")
