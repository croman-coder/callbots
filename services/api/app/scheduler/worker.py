"""Celery: worker + beat.

    beat   -> agenda las tareas periódicas
    worker -> las ejecuta (sync con Bitrix, originar llamadas, analizar)
"""

from __future__ import annotations

import logging

from celery import Celery
from celery.schedules import crontab

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)

celery_app = Celery(
    "callbot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.scheduler.tasks"],
)

celery_app.conf.update(
    timezone=settings.tz,
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=86400,
    beat_schedule={
        # Trae registros nuevos de Bitrix
        "sync-bitrix": {
            "task": "callbot.sync_bitrix",
            "schedule": crontab(
                minute=f"*/{max(1, settings.bitrix_poll_interval_minutes)}"
            ),
        },
        # Dispara las llamadas que ya vencieron
        "dispatch-due-calls": {
            "task": "callbot.dispatch_due_calls",
            "schedule": 60.0,
        },
        # Rescata llamadas colgadas y reprograma las que no atendieron
        "watchdog": {
            "task": "callbot.watchdog",
            "schedule": 120.0,
        },
        # Reintenta los envíos a Bitrix que fallaron
        "retry-bitrix-sync": {
            "task": "callbot.retry_failed_writebacks",
            "schedule": 600.0,
        },
    },
)
