"""Revisión a mano: corregir el resultado de una llamada, corregir una respuesta mal transcrita y comentar.

- **Resultado**: el sistema dice que una llamada es exitosa cuando el cliente completó la encuesta. Quien revisa puede
  marcarla «exitosa» o «no exitosa» con un motivo (por ejemplo, completó pero contestó en joda; o cortó en la última
  pregunta pero dijo todo lo importante). Queda quién y cuándo, y se puede volver a la calificación del sistema.
- **Respuesta**: si Whisper entendió mal («diez» por «seis»), se corrige el valor. Se guarda el que había, quién y
  cuándo; el puntaje y el seguimiento se recalculan y el resultado corregido se vuelve a mandar a Bitrix.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_

from app.models import CallAttempt, CallOutcome, QuestionType

REVISIONES = {"exitosa": "Exitosa", "no_exitosa": "No exitosa"}


class RevisionError(ValueError):
    """El motivo se puede mostrar tal cual."""


def exitosa(call: CallAttempt) -> bool:
    """La calificación final: la revisión a mano si hay, si no la del sistema (completó la encuesta)."""
    if call.revision in REVISIONES:
        return call.revision == "exitosa"
    return call.outcome is CallOutcome.COMPLETED


def condicion_exitosas():
    """Lo mismo que `exitosa`, como condición SQL para filtrar."""
    return or_(
        CallAttempt.revision == "exitosa",
        and_(CallAttempt.revision.is_(None), CallAttempt.outcome == CallOutcome.COMPLETED),
    )


def revisar(call: CallAttempt, veredicto: str, motivo: str, usuario: str | None) -> None:
    motivo = " ".join((motivo or "").split())[:1000]
    if veredicto in ("", "sistema"):
        call.revision = call.revision_motivo = call.revision_por = None
        call.revision_at = None
        return
    if veredicto not in REVISIONES:
        raise RevisionError("Elegí exitosa o no exitosa.")
    if len(motivo) < 3:
        raise RevisionError("Escribí el motivo: queda registrado junto a la revisión.")
    call.revision, call.revision_motivo = veredicto, motivo
    call.revision_por, call.revision_at = (usuario or None), datetime.now(timezone.utc)


def valor_de(texto: str, qtype: QuestionType) -> float:
    """El valor que escribió quien corrige, validado según el tipo de pregunta."""
    t = (texto or "").strip().lower().replace(",", ".")
    if qtype is QuestionType.OPEN:
        raise RevisionError("Las preguntas de respuesta libre no tienen valor para corregir.")
    if qtype is QuestionType.YES_NO:
        if t in ("si", "sí", "1", "true"):
            return 1.0
        if t in ("no", "0", "false"):
            return 0.0
        raise RevisionError("Elegí sí o no.")
    try:
        n = float(t)
    except ValueError as exc:
        raise RevisionError("Escribí un número.") from exc
    rango = {QuestionType.SCALE_1_10: (0, 10), QuestionType.SCALE_1_5: (1, 5)}.get(qtype)
    if rango and not rango[0] <= n <= rango[1]:
        raise RevisionError(f"Tiene que ser un número del {rango[0]} al {rango[1]}.")
    return n


def corregir(answer, valor: float, usuario: str | None) -> None:  # noqa: ANN001 - Answer
    if answer.valor_original is None and answer.value_source != "manual":
        answer.valor_original = answer.value_numeric
    answer.value_numeric = valor
    answer.value_source = "manual"
    answer.corregido_por = usuario or None
    answer.corregido_at = datetime.now(timezone.utc)
