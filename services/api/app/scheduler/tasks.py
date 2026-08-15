"""Tareas del callbot.

Ciclo de vida de un destinatario:

    PENDING ──(vence T0+48h y hay ventana)──► QUEUED ──► CALLING
       ▲                                                    │
       └──── SCHEDULED (reintento) ◄── no atendió ──────────┤
                                                            │
                                         COMPLETED ◄────────┘
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import func, select

from app.bitrix.client import BitrixClient
from app.bitrix.sync import sync_all_campaigns
from app.config import settings
from app.db import session_scope
from app.models import (
    CallAnalysis,
    CallAttempt,
    CallOutcome,
    Campaign,
    SurveyTarget,
    TargetStatus,
)
from app.scheduling import is_within_window, next_call_slot, parse_days, parse_hhmm
from app.services.analysis import analyze_call
from app.services.asterisk_ari import AriClient, AriError
from app.services.writeback import push_result_to_bitrix

log = logging.getLogger(__name__)

ENTITY_TYPE_NAMES = {1: "LEAD", 2: "DEAL", 3: "CONTACT", 4: "COMPANY"}


# ---------------------------------------------------------------------------
# 1. Sincronización con Bitrix
# ---------------------------------------------------------------------------
@shared_task(name="callbot.sync_bitrix")
def sync_bitrix() -> list[dict]:
    if not settings.bitrix_webhook_url.startswith("http"):
        # Sin webhook la sincronización no puede hacer nada, y correrla igual
        # llena el log de errores cada 15 minutos por algo que ya sabemos.
        log.debug("Sin BITRIX_WEBHOOK_URL: se saltea la sincronización")
        return []

    with session_scope() as db:
        reports = sync_all_campaigns(db)
        return [r.as_dict() for r in reports]


# ---------------------------------------------------------------------------
# 2. Disparo de llamadas vencidas
# ---------------------------------------------------------------------------
@shared_task(name="callbot.dispatch_due_calls")
def dispatch_due_calls() -> dict:
    now = datetime.now(timezone.utc)
    skipped_window = 0
    truncated = False
    # Los ids se encolan recién después de cerrar la transacción: ver abajo.
    a_encolar: list[int] = []

    with session_scope() as db:
        in_flight = db.scalar(
            select(func.count(SurveyTarget.id)).where(
                SurveyTarget.status.in_([TargetStatus.QUEUED, TargetStatus.CALLING])
            )
        ) or 0

        free_slots = settings.max_concurrent_calls - in_flight
        if free_slots <= 0:
            log.debug("Sin cupo: %d llamadas en curso", in_flight)
            return {"dispatched": 0, "in_flight": in_flight}

        # El tope no puede ser solo proporcional al cupo: los que caen fuera de
        # ventana se lo comen y dejan sin despachar a los que sí están en
        # horario más abajo en la cola. El colchón fijo cubre ese caso, y si
        # aun así se corta queda registrado en vez de pasar inadvertido.
        tope = free_slots * 3 + 50
        candidates = db.scalars(
            select(SurveyTarget)
            .where(
                SurveyTarget.status.in_([TargetStatus.PENDING, TargetStatus.SCHEDULED]),
                SurveyTarget.scheduled_at <= now,
            )
            .order_by(SurveyTarget.scheduled_at)
            .limit(tope)
            # Dos corridas solapadas del despachador leerían el mismo cupo y
            # despacharían ambas la tanda entera, pasándose de
            # MAX_CONCURRENT_CALLS. Con el lock, la segunda saltea lo que la
            # primera ya tomó en vez de duplicarlo.
            .with_for_update(skip_locked=True)
        ).all()
        truncated = len(candidates) == tope

        for target in candidates:
            if len(a_encolar) >= free_slots:
                break

            campaign = target.campaign
            if campaign is None or not campaign.is_active:
                continue

            window_start = parse_hhmm(campaign.call_window_start, settings.call_window_start)
            window_end = parse_hhmm(campaign.call_window_end, settings.call_window_end)
            allowed_days = parse_days(campaign.call_window_days)

            # Un destinatario sin intentos disponibles se cierra antes de mirar
            # la ventana: si no, cada corrida lo reprograma y nunca llega a
            # cerrarse mientras esté fuera de horario.
            if target.attempts >= campaign.max_attempts:
                target.status = TargetStatus.NO_ANSWER
                continue

            if not is_within_window(
                now, window_start, window_end, allowed_days, settings.timezone
            ):
                # Fuera de horario: reprogramamos al próximo hueco válido
                target.scheduled_at = next_call_slot(
                    now, window_start, window_end, allowed_days, settings.timezone
                )
                target.status = TargetStatus.SCHEDULED
                skipped_window += 1
                continue

            target.status = TargetStatus.QUEUED
            a_encolar.append(target.id)

    # Fuera del `with`: la transacción ya cerró y el estado QUEUED está escrito.
    # Encolar adentro es una carrera perdida — el worker puede tomar la tarea
    # antes del commit, leer el estado viejo, descartar la llamada, y dejar al
    # destinatario en QUEUED, que el despachador ya no selecciona. Se queda
    # colgado para siempre y sin ruido.
    for target_id in a_encolar:
        place_call.delay(target_id)

    if skipped_window:
        log.info("%d destinatarios reprogramados por estar fuera de ventana", skipped_window)
    if a_encolar:
        log.info("%d llamadas encoladas", len(a_encolar))
    if truncated:
        log.warning(
            "Se leyeron %d candidatos, el tope de la consulta: puede haber más "
            "esperando. Se despachan en la próxima corrida.", tope,
        )

    return {"dispatched": len(a_encolar), "skipped_window": skipped_window}


# ---------------------------------------------------------------------------
# 3. Originar una llamada
# ---------------------------------------------------------------------------
@shared_task(name="callbot.place_call", bind=True, max_retries=2)
def place_call(self, target_id: int) -> dict:  # noqa: ANN001
    with session_scope() as db:
        # Con la fila bloqueada, dos workers que reciban la misma tarea se
        # serializan: el segundo ve el estado que dejó el primero en vez de
        # marcar un intento en paralelo.
        target = db.scalar(
            select(SurveyTarget).where(SurveyTarget.id == target_id).with_for_update()
        )
        if target is None:
            return {"error": "target inexistente"}

        if target.status is not TargetStatus.QUEUED:
            log.warning("Target %s en estado %s, no se llama", target_id, target.status)
            return {"skipped": True, "status": target.status.value}

        # `task_acks_late` hace que Celery reentregue la tarea si el worker
        # muere antes del ack. Si murió después de marcar pero antes del
        # commit, el estado sigue en QUEUED y sin esta guarda le llamaríamos al
        # cliente una segunda vez. Un intento abierto es la señal de que la
        # llamada ya salió.
        abierto = db.scalar(
            select(func.count(CallAttempt.id)).where(
                CallAttempt.target_id == target.id,
                CallAttempt.ended_at.is_(None),
                CallAttempt.started_at >= datetime.now(timezone.utc) - timedelta(minutes=10),
            )
        ) or 0
        if abierto:
            log.warning(
                "Target %s ya tiene un intento en curso, no se vuelve a llamar", target_id
            )
            return {"skipped": True, "reason": "intento en curso"}

        if not target.phone:
            target.status = TargetStatus.SKIPPED
            target.last_error = "Sin teléfono"
            return {"skipped": True, "reason": "sin teléfono"}

        target.attempts += 1
        attempt = CallAttempt(
            target_id=target.id,
            attempt_number=target.attempts,
            dialed_number=target.phone,
            started_at=datetime.now(timezone.utc),
        )
        db.add(attempt)
        db.flush()

        session_uuid = attempt.session_uuid
        phone = target.phone
        entity_type_id = target.bitrix_entity_type_id
        entity_id = target.bitrix_entity_id

        try:
            with AriClient() as ari:
                channel_id = ari.originate(phone=phone, session_uuid=session_uuid)
            attempt.asterisk_channel_id = channel_id
            target.status = TargetStatus.CALLING
            target.last_error = None

            # Recién acá se registra en Bitrix. Hacerlo antes del originate
            # dejaba en el historial del cliente llamadas que nunca salieron.
            if settings.bitrix_register_call:
                try:
                    with BitrixClient() as client:
                        attempt.bitrix_call_id = client.register_call(
                            user_id=settings.bitrix_telephony_user_id,
                            phone_number=phone,
                            call_start_date=attempt.started_at,
                            crm_entity_type=ENTITY_TYPE_NAMES.get(entity_type_id),
                            crm_entity_id=(
                                entity_id if entity_type_id in ENTITY_TYPE_NAMES else None
                            ),
                        )
                except Exception as exc:  # noqa: BLE001 - nunca frenar la llamada por esto
                    log.warning("No se registró la llamada en Bitrix: %s", exc)

            log.info(
                "Llamando a %s (target=%s, intento=%s, canal=%s)",
                phone, target.id, target.attempts, channel_id,
            )
            return {"target_id": target.id, "channel": channel_id}

        except AriError as exc:
            log.error("Fallo el originate para target %s: %s", target.id, exc)
            attempt.outcome = CallOutcome.FAILED
            attempt.ended_at = datetime.now(timezone.utc)
            target.last_error = str(exc)[:1000]
            _schedule_retry_or_finish(target, TargetStatus.FAILED)
            return {"error": str(exc)}


def _schedule_retry_or_finish(target: SurveyTarget, final_status: TargetStatus) -> None:
    """Reprograma el próximo intento o cierra el destinatario si ya no quedan."""
    campaign = target.campaign
    max_attempts = campaign.max_attempts if campaign else settings.max_call_attempts
    interval = (
        campaign.retry_interval_minutes if campaign else settings.retry_interval_minutes
    )

    if target.attempts >= max_attempts:
        target.status = final_status
        log.info(
            "Target %s cerrado como %s tras %d intentos",
            target.id, final_status.value, target.attempts,
        )
        return

    window_start = parse_hhmm(
        campaign.call_window_start if campaign else "09:00", settings.call_window_start
    )
    window_end = parse_hhmm(
        campaign.call_window_end if campaign else "19:00", settings.call_window_end
    )
    allowed_days = parse_days(campaign.call_window_days if campaign else "0,1,2,3,4,5")

    retry_at = datetime.now(timezone.utc) + timedelta(minutes=interval)
    target.scheduled_at = next_call_slot(
        retry_at, window_start, window_end, allowed_days, settings.timezone
    )
    target.status = TargetStatus.SCHEDULED
    log.info(
        "Target %s reprogramado para %s (intento %d/%d)",
        target.id, target.scheduled_at, target.attempts + 1, max_attempts,
    )


# ---------------------------------------------------------------------------
# 4. Cierre y análisis
# ---------------------------------------------------------------------------
@shared_task(name="callbot.finalize_call")
def finalize_call(call_id: int) -> dict:
    """Analiza la llamada terminada y publica el resultado en Bitrix."""
    with session_scope() as db:
        call = db.get(CallAttempt, call_id)
        if call is None:
            return {"error": "llamada inexistente"}

        target = call.target

        if call.outcome is CallOutcome.COMPLETED and call.answers:
            analyze_call(db, call)
            db.flush()
            push_result_to_bitrix(db, call)
            target.status = TargetStatus.COMPLETED
            log.info(
                "Encuesta completada: target=%s puntaje=%s",
                target.id,
                call.analysis.satisfaction_score if call.analysis else None,
            )

        elif call.outcome is CallOutcome.PARTIAL and call.answers:
            # Contestó algunas preguntas: guardamos lo que hay y no reintentamos
            analyze_call(db, call)
            db.flush()
            push_result_to_bitrix(db, call)
            target.status = TargetStatus.COMPLETED
            log.info("Encuesta parcial guardada para target=%s", target.id)

        else:
            _schedule_retry_or_finish(target, TargetStatus.NO_ANSWER)

        return {"call_id": call_id, "target_status": target.status.value}


# ---------------------------------------------------------------------------
# 5. Watchdog
# ---------------------------------------------------------------------------
@shared_task(name="callbot.watchdog")
def watchdog() -> dict:
    """Rescata llamadas que quedaron colgadas.

    Casos: nadie atendió (el canal nunca llegó al voice-agent), el contenedor
    del voice-agent murió a mitad de la encuesta, o Asterisk se reinició.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.call_answer_timeout_seconds)
    recovered = 0

    with session_scope() as db:
        stuck = db.scalars(
            select(CallAttempt)
            .join(SurveyTarget)
            .where(
                SurveyTarget.status == TargetStatus.CALLING,
                CallAttempt.ended_at.is_(None),
                CallAttempt.started_at < cutoff,
            )
        ).all()

        for call in stuck:
            # ¿Sigue vivo el canal en Asterisk? Si sí, la llamada está en curso.
            if call.asterisk_channel_id:
                try:
                    with AriClient() as ari:
                        if ari.channel_state(call.asterisk_channel_id) is not None:
                            continue
                except AriError:
                    pass

            call.ended_at = now
            call.duration_seconds = int((now - call.started_at).total_seconds())

            if call.answers:
                call.outcome = CallOutcome.PARTIAL
            elif call.answered_at:
                call.outcome = CallOutcome.PARTIAL
            else:
                call.outcome = CallOutcome.NO_ANSWER

            if settings.bitrix_register_call and call.bitrix_call_id:
                try:
                    with BitrixClient() as client:
                        client.finish_call(
                            call_id=call.bitrix_call_id,
                            user_id=settings.bitrix_telephony_user_id,
                            duration=call.duration_seconds or 0,
                            status_code="304" if call.outcome is CallOutcome.NO_ANSWER else "200",
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("No se cerró la llamada en Bitrix: %s", exc)

            db.flush()
            recovered += 1
            finalize_call.delay(call.id)

    if recovered:
        log.info("Watchdog recuperó %d llamadas colgadas", recovered)
    return {"recovered": recovered}


# ---------------------------------------------------------------------------
# 6. Reintento de escritura en Bitrix
# ---------------------------------------------------------------------------
@shared_task(name="callbot.retry_failed_writebacks")
def retry_failed_writebacks() -> dict:
    retried = 0
    with session_scope() as db:
        pending = db.scalars(
            select(CallAnalysis)
            .where(CallAnalysis.synced_to_bitrix.is_(False))
            .order_by(CallAnalysis.created_at)
            .limit(20)
        ).all()

        for analysis in pending:
            if push_result_to_bitrix(db, analysis.call):
                retried += 1

    if retried:
        log.info("Reenviados %d resultados a Bitrix", retried)
    return {"retried": retried}


# ---------------------------------------------------------------------------
# Utilidad manual
# ---------------------------------------------------------------------------
@shared_task(name="callbot.call_now")
def call_now(target_id: int) -> dict:
    """Fuerza una llamada ignorando la ventana horaria (botón del panel)."""
    with session_scope() as db:
        target = db.get(SurveyTarget, target_id)
        if target is None:
            return {"error": "target inexistente"}
        target.status = TargetStatus.QUEUED
        db.flush()
    return place_call(target_id)


@shared_task(name="callbot.reanalyze_call")
def reanalyze_call(call_id: int) -> dict:
    """Vuelve a correr el LLM sobre una llamada ya guardada."""
    with session_scope() as db:
        call = db.get(CallAttempt, call_id)
        if call is None:
            return {"error": "llamada inexistente"}
        analyze_call(db, call)
        db.flush()
        push_result_to_bitrix(db, call)
        return {"call_id": call_id, "ok": True}
