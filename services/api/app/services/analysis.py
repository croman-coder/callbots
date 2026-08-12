"""Análisis post-llamada con el LLM local (Ollama).

Corre después de colgar, así que la latencia no importa. Hace dos cosas:

1. Calcula el puntaje de satisfacción con las respuestas ya interpretadas por
   reglas (esto no depende del LLM y siempre funciona).
2. Le pide al LLM sentimiento, resumen, temas y si el caso necesita que
   alguien vuelva a llamar. Si Ollama no responde, el punto 1 igual se guarda.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Answer, CallAnalysis, CallAttempt, QuestionType
from app.services.scoring import interpret, to_percentage

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un analista de calidad de atención al cliente de un taller \
mecánico en Paraguay. Recibís la transcripción de una encuesta telefónica \
automática y devolvés un análisis objetivo en JSON.

Reglas:
- Respondé SOLO con un objeto JSON válido, sin texto adicional.
- El resumen debe tener como máximo 2 oraciones, en español rioplatense neutro.
- "sentiment" solo puede ser: "positivo", "neutral" o "negativo".
- "requires_followup" es true solo si el cliente expresó un problema concreto \
sin resolver, pidió que lo contacten, o mostró enojo claro.
- "topics" son entre 1 y 4 etiquetas cortas en minúscula (ej: "demora", \
"precio", "trato del personal", "calidad del trabajo").

Formato exacto:
{"sentiment": "...", "summary": "...", "topics": ["..."], \
"requires_followup": false, "followup_reason": null}"""


def build_transcript(call: CallAttempt) -> str:
    """Arma el diálogo pregunta/respuesta en texto plano."""
    lines: list[str] = []
    for answer in sorted(call.answers, key=lambda a: a.question.position):
        lines.append(f"P{answer.question.position}: {answer.question.text}")
        lines.append(f"R{answer.question.position}: {answer.transcript or '(sin respuesta)'}")
    return "\n".join(lines)


def compute_satisfaction(answers: list[Answer]) -> float | None:
    """Promedio 0-100 de las preguntas marcadas como puntuables."""
    percentages: list[float] = []
    for answer in answers:
        if not answer.question.counts_for_score or answer.value_numeric is None:
            continue
        pct = to_percentage(answer.value_numeric, answer.question.qtype)
        if pct is not None:
            percentages.append(pct)

    if not percentages:
        return None
    return round(sum(percentages) / len(percentages), 1)


def _fallback_sentiment(score: float | None) -> str:
    if score is None:
        return "neutral"
    if score >= 70:
        return "positivo"
    if score >= 40:
        return "neutral"
    return "negativo"


def query_ollama(transcript: str, timeout: float = 120.0) -> dict[str, Any] | None:
    """Consulta el LLM local. Devuelve None si no está disponible."""
    if not settings.ollama_enabled:
        return None

    payload = {
        "model": settings.ollama_model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcripción de la encuesta:\n\n{transcript}"},
        ],
    }

    try:
        response = httpx.post(
            f"{settings.ollama_url.rstrip('/')}/api/chat",
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content", "")
        return json.loads(content)
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("Ollama no pudo analizar la llamada: %s", exc)
        return None


def enrich_open_answers(call: CallAttempt) -> None:
    """Re-interpreta con reglas las respuestas que quedaron sin valor.

    Durante la llamada priorizamos la velocidad; acá tenemos tiempo de aplicar
    el mapeo cualitativo completo sobre lo que quedó sin puntuar.
    """
    for answer in call.answers:
        if answer.value_numeric is not None:
            continue
        if answer.question.qtype is QuestionType.OPEN:
            continue
        result = interpret(answer.transcript, answer.question.qtype)
        if result.understood and result.value is not None:
            answer.value_numeric = result.value
            answer.value_source = "rules_post"


def analyze_call(db: Session, call: CallAttempt) -> CallAnalysis:
    """Genera (o regenera) el análisis de una llamada."""
    enrich_open_answers(call)

    score = compute_satisfaction(call.answers)
    transcript = build_transcript(call)

    llm_result = query_ollama(transcript) if transcript.strip() else None

    analysis = call.analysis or CallAnalysis(call_id=call.id)
    analysis.satisfaction_score = score

    if llm_result:
        sentiment = str(llm_result.get("sentiment", "")).lower()
        analysis.sentiment = (
            sentiment if sentiment in ("positivo", "neutral", "negativo")
            else _fallback_sentiment(score)
        )
        analysis.summary = (llm_result.get("summary") or None)
        topics = llm_result.get("topics")
        analysis.topics = topics if isinstance(topics, list) else None
        analysis.requires_followup = bool(llm_result.get("requires_followup"))
        analysis.followup_reason = llm_result.get("followup_reason") or None
        analysis.model_used = settings.ollama_model
    else:
        analysis.sentiment = _fallback_sentiment(score)
        analysis.summary = None
        analysis.model_used = None
        # Sin LLM, marcamos seguimiento por puntaje bajo
        analysis.requires_followup = score is not None and score < 40
        if analysis.requires_followup:
            analysis.followup_reason = f"Puntaje bajo ({score}/100)"

    if analysis.id is None:
        db.add(analysis)
    db.flush()
    return analysis
