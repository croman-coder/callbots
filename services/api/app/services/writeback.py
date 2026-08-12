"""Devolución de resultados a Bitrix24.

Deja el resultado donde el equipo lo va a ver: un comentario en el timeline del
registro y, opcionalmente, el puntaje en un campo propio.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.bitrix.client import BitrixClient, BitrixError
from app.config import settings
from app.models import CallAttempt, QuestionType
from app.services.scoring import SATISFACTORY_MIN, is_satisfactory, to_scale_10

log = logging.getLogger(__name__)

SENTIMENT_ICON = {"positivo": "🟢", "neutral": "🟡", "negativo": "🔴"}


def _format_answer(value: float | None, qtype: QuestionType, transcript: str | None) -> str:
    if qtype is QuestionType.OPEN or value is None:
        return (transcript or "(sin respuesta)").strip()
    if qtype is QuestionType.YES_NO:
        return "Sí" if value >= 0.5 else "No"
    if qtype is QuestionType.SCALE_1_5:
        return f"{value:.0f}/5"
    if qtype is QuestionType.SCALE_1_10:
        return f"{value:.0f}/10"
    return f"{value:.0f}"


def _answer_flag(answer) -> str:  # noqa: ANN001
    """Marca visual por respuesta: solo 9 y 10 pasan."""
    if not answer.question.counts_for_score or answer.value_numeric is None:
        return ""
    score = to_scale_10(answer.value_numeric, answer.question.qtype)
    if score is None:
        return ""
    return "  ✅" if is_satisfactory(score) else "  ⚠"


def build_comment(call: CallAttempt) -> str:
    """Arma el comentario en BBCode, que es lo que renderiza el timeline."""
    target = call.target
    analysis = call.analysis

    lines: list[str] = []

    # La advertencia va primero: es lo que hay que accionar, y el timeline de
    # Bitrix se lee de arriba y truncado.
    if analysis and analysis.requires_followup:
        reason = analysis.followup_reason or "el cliente manifestó un problema"
        lines += [
            "[B]⚠ ADVERTENCIA — REQUIERE SEGUIMIENTO[/B]",
            reason,
            "",
        ]

    lines += ["[B]Encuesta de satisfacción automática[/B]", ""]

    if analysis and analysis.satisfaction_score is not None:
        icon = SENTIMENT_ICON.get(analysis.sentiment or "", "")
        verdict = (
            "satisfactorio"
            if is_satisfactory(analysis.satisfaction_score)
            else f"por debajo de {SATISFACTORY_MIN:.0f}"
        )
        lines.append(
            f"{icon} [B]Satisfacción: {analysis.satisfaction_score:.1f}/10[/B] "
            f"({verdict}, {analysis.sentiment or 'sin clasificar'})"
        )
        lines.append("")

    lines.append("[B]Respuestas:[/B]")
    for answer in sorted(call.answers, key=lambda a: a.question.position):
        formatted = _format_answer(
            answer.value_numeric, answer.question.qtype, answer.transcript
        )
        lines.append(f"[LIST][*]{answer.question.text}")
        lines.append(f"→ {formatted}{_answer_flag(answer)}[/LIST]")

    if analysis and analysis.summary:
        lines += ["", "[B]Resumen:[/B]", analysis.summary]

    if analysis and analysis.topics:
        lines += ["", "[B]Temas:[/B] " + ", ".join(str(t) for t in analysis.topics)]

    duration = call.duration_seconds or 0
    lines += [
        "",
        f"[I]Llamada del {call.started_at:%d/%m/%Y %H:%M} · "
        f"{duration // 60}m {duration % 60}s · "
        f"intento {call.attempt_number} · "
        f"{call.questions_answered}/{call.questions_asked} preguntas respondidas[/I]",
    ]

    if target and target.phone:
        lines.append(f"[I]Teléfono: {target.phone}[/I]")

    return "\n".join(lines)


def push_result_to_bitrix(db: Session, call: CallAttempt) -> bool:
    """Escribe el resultado en Bitrix. Devuelve True si salió todo bien."""
    analysis = call.analysis
    target = call.target

    if target is None:
        log.error("La llamada %s no tiene destinatario asociado", call.id)
        return False

    ok = True
    error_parts: list[str] = []

    try:
        with BitrixClient() as client:
            # 1) Comentario en el timeline
            if settings.bitrix_timeline_comment:
                try:
                    client.add_timeline_comment(
                        entity_type_id=target.bitrix_entity_type_id,
                        entity_id=target.bitrix_entity_id,
                        comment=build_comment(call),
                    )
                    log.info(
                        "Comentario publicado en %s#%s",
                        target.bitrix_entity_type_id,
                        target.bitrix_entity_id,
                    )
                except BitrixError as exc:
                    ok = False
                    error_parts.append(f"timeline: {exc}")

            # 2) Puntaje en un campo propio del registro
            field = settings.bitrix_field_score_writeback
            if field and analysis and analysis.satisfaction_score is not None:
                try:
                    client.update_item(
                        entity_type_id=target.bitrix_entity_type_id,
                        item_id=target.bitrix_entity_id,
                        fields={field: analysis.satisfaction_score},
                    )
                except BitrixError as exc:
                    ok = False
                    error_parts.append(f"campo {field}: {exc}")

            # 3) Cierre de la llamada en el módulo de telefonía
            if settings.bitrix_register_call and call.bitrix_call_id:
                client.finish_call(
                    call_id=call.bitrix_call_id,
                    user_id=settings.bitrix_telephony_user_id,
                    duration=call.duration_seconds or 0,
                    status_code="200",
                )

    except BitrixError as exc:
        ok = False
        error_parts.append(str(exc))

    if analysis:
        analysis.synced_to_bitrix = ok
        analysis.sync_error = "; ".join(error_parts) if error_parts else None
        db.flush()

    if not ok:
        log.error("Fallo escribiendo en Bitrix la llamada %s: %s", call.id, error_parts)

    return ok
