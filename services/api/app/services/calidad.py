"""Qué tan bien salió una llamada, del lado del bot.

No mide al cliente (eso es la satisfacción), mide la conversación: si el bot le hizo
repetir, si quedaron preguntas sin respuesta, si se cortó a mitad. Sirve para encontrar
las llamadas que hay que escuchar para mejorar el guion o la voz.
Reglas simples y explicables: cada descuento dice por qué.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Calidad:
    puntaje: int | None
    motivos: list[str] = field(default_factory=list)

    @property
    def nivel(self) -> str:
        if self.puntaje is None:
            return ""
        return "ok" if self.puntaje >= 80 else "warn" if self.puntaje >= 50 else "bad"


def _valor(o, nombre, defecto=None):
    v = getattr(o, nombre, defecto)
    return getattr(v, "value", v)


def calidad_de(call) -> Calidad:
    """None si no hubo conversación (no atendió, falló la llamada)."""
    resultado = _valor(call, "outcome")
    preguntadas = getattr(call, "questions_asked", 0) or 0
    if resultado in (None, "no_answer", "busy", "rejected", "failed", "voicemail") or preguntadas == 0:
        return Calidad(None)

    puntaje = 100
    motivos: list[str] = []
    respuestas = list(getattr(call, "answers", []) or [])

    sin_respuesta = max(0, preguntadas - (getattr(call, "questions_answered", 0) or 0))
    if sin_respuesta:
        puntaje -= 15 * sin_respuesta
        motivos.append(f"{sin_respuesta} pregunta{'s' if sin_respuesta != 1 else ''} sin respuesta")

    repreguntas = sum(int(getattr(a, "retries_used", 0) or 0) for a in respuestas)
    if repreguntas:
        puntaje -= 7 * repreguntas
        motivos.append(f"{repreguntas} repregunta{'s' if repreguntas != 1 else ''}: no se le entendió")

    dudosas = [a for a in respuestas if (getattr(a, "asr_confidence", None) or 1.0) < 0.6]
    if dudosas:
        puntaje -= 5 * len(dudosas)
        motivos.append(f"{len(dudosas)} respuesta{'s' if len(dudosas) != 1 else ''} con audio dudoso")

    if resultado == "partial" and sin_respuesta:
        puntaje -= 10
        motivos.append("se cortó antes de terminar")

    return Calidad(max(0, min(100, puntaje)), motivos)
