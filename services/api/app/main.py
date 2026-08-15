"""Punto de entrada de la API + panel de administración."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.routers import admin, internal, simulator

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

app.include_router(internal.router)
app.include_router(admin.router)
app.include_router(simulator.router)


@app.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    """Liveness para el healthcheck de Docker. Sin dependencias externas.

    El chequeo profundo (Bitrix, Asterisk, Ollama) está en /health-detail, que
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
    if settings.admin_password in ("admin", "cambiar_esta_password"):
        log.warning("ADMIN_PASSWORD tiene el valor por default: cambialo en producción")
