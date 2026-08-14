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
from app.services.scoring import (
    SATISFACTORY_MIN,
    interpret,
    is_satisfactory,
    to_scale_10,
)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Sos un analista de calidad de atención al cliente de un taller \
mecánico en Paraguay. Recibís la transcripción de una encuesta telefónica \
automática y devolvés un análisis objetivo en JSON.

Las preguntas se puntúan del 0 al 10. Solo 9 y 10 cuentan como cliente \
conforme; de 8 para abajo hay algo que mejorar, incluso si el cliente fue \
amable al responder.

Reglas:
- Respondé SOLO con un objeto JSON válido, sin texto adicional.
- El resumen debe tener como máximo 2 oraciones, en español rioplatense neutro.
- "sentiment" solo puede ser: "positivo", "neutral" o "negativo". Reservá \
"positivo" para cuando las respuestas fueron 9 o 10.
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


def _scored(answers: list[Answer]) -> list[tuple[Answer, float]]:
    """Respuestas puntuables con su valor ya en escala 0-10."""
    out: list[tuple[Answer, float]] = []
    for answer in answers:
        if not answer.question.counts_for_score or answer.value_numeric is None:
            continue
        score = to_scale_10(answer.value_numeric, answer.question.qtype)
        if score is not None:
            out.append((answer, score))
    return out


def compute_satisfaction(answers: list[Answer]) -> float | None:
    """Promedio en escala 0-10 de las preguntas marcadas como puntuables."""
    scored = _scored(answers)
    if not scored:
        return None
    return round(sum(score for _, score in scored) / len(scored), 1)


def low_scores(answers: list[Answer]) -> list[tuple[str, float]]:
    """Preguntas por debajo del umbral. Son el motivo concreto de la advertencia."""
    return [
        (answer.question.text, score)
        for answer, score in _scored(answers)
        if not is_satisfactory(score)
    ]


def _fallback_sentiment(score: float | None) -> str:
    """Clasificación por puntaje cuando el LLM no está disponible.

    Los cortes siguen el criterio del negocio: 9-10 conforme, 7-8 tibio,
    6 o menos disconforme.
    """
    if score is None:
        return "neutral"
    if score >= SATISFACTORY_MIN:
        return "positivo"
    if score >= 7:
        return "neutral"
    return "negativo"


def build_system_prompt(extra: str | None) -> str:
    """Prompt base + lo que haya cargado la campaña.

    Las instrucciones de la campaña se agregan DESPUÉS del prompt base y no lo
    reemplazan: el formato JSON de la respuesta es un contrato con el código
    que la parsea, y dejar que se pise convierte cualquier prompt mal escrito
    en un análisis perdido en silencio.
    """
    extra = (extra or "").strip()
    if not extra:
        return SYSTEM_PROMPT

    return (
        f"{SYSTEM_PROMPT}\n\n"
        "Contexto e instrucciones adicionales de esta campaña "
        "(respetá igual el formato JSON de arriba):\n"
        f"{extra}"
    )


def query_ollama(
    transcript: str, timeout: float = 120.0, extra_prompt: str | None = None
) -> dict[str, Any] | None:
    """Consulta el LLM local. Devuelve None si no está disponible."""
    if not settings.ollama_enabled:
        return None

    payload = {
        "model": settings.ollama_model,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
        "messages": [
            {"role": "system", "content": build_system_prompt(extra_prompt)},
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

    campaign = call.target.campaign if call.target else None
    llm_result = (
        query_ollama(transcript, extra_prompt=getattr(campaign, "analysis_prompt", None))
        if transcript.strip()
        else None
    )

    analysis = call.analysis or CallAnalysis(call_id=call.id)
    analysis.satisfaction_score = score

    # La regla del negocio manda sobre el criterio del LLM: cualquier respuesta
    # por debajo de 9 es advertencia, opine lo que opine el modelo.
    below = low_scores(call.answers)
    score_warning = None
    if below:
        detail = "; ".join(f"{text[:60]} = {value:.0f}/10" for text, value in below)
        score_warning = (
            f"{len(below)} respuesta(s) bajo {SATISFACTORY_MIN:.0f}/10 → {detail}"
        )

    if llm_result:
        sentiment = str(llm_result.get("sentiment", "")).lower()
        analysis.sentiment = (
            sentiment if sentiment in ("positivo", "neutral", "negativo")
            else _fallback_sentiment(score)
        )
        analysis.summary = (llm_result.get("summary") or None)
        topics = llm_result.get("topics")
        analysis.topics = topics if isinstance(topics, list) else None
        analysis.model_used = settings.ollama_model

        analysis.requires_followup = bool(below) or bool(
            llm_result.get("requires_followup")
        )
        reasons = [score_warning, llm_result.get("followup_reason")]
        analysis.followup_reason = " | ".join(r for r in reasons if r) or None
    else:
        analysis.sentiment = _fallback_sentiment(score)
        analysis.summary = None
        analysis.model_used = None
        analysis.requires_followup = bool(below)
        analysis.followup_reason = score_warning

    if analysis.id is None:
        db.add(analysis)
    db.flush()
    return analysis
