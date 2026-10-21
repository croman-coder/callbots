"""«Preguntale a tus llamadas»: Claude Haiku responde sobre las encuestas filtradas.

Se le pasa un resumen en texto de cada llamada (respuestas, transcripción, análisis) y la
pregunta de quien opera. Sin teléfonos: el modelo no los necesita para responder sobre
calidad y así no salen del sistema. Solo el primer nombre, para poder citar casos.
"""

from __future__ import annotations

import re

from app.config import settings
from app.services import llm

MAX_CARACTERES = 60_000
# Cada pregunta cuesta una llamada a Claude con hasta 60.000 caracteres de contexto.
MAX_PREGUNTAS_POR_HORA = 20
_hechas: dict[str, list[float]] = {}


def permitir(usuario: str, ahora: float | None = None) -> bool:
    """False si esta persona ya hizo MAX_PREGUNTAS_POR_HORA en la última hora."""
    import time

    ahora = time.monotonic() if ahora is None else ahora
    recientes = [t for t in _hechas.get(usuario, []) if ahora - t < 3600]
    if len(recientes) >= MAX_PREGUNTAS_POR_HORA:
        _hechas[usuario] = recientes
        return False
    recientes.append(ahora)
    _hechas[usuario] = recientes
    return True
MAX_LLAMADAS = 200

SISTEMA = """Sos analista de calidad de la posventa de Santa Rosa Paraguay (concesionaria \
multimarca). Te paso las encuestas telefónicas de satisfacción que hizo el bot del taller, \
una por bloque, y una pregunta de alguien del equipo.

Reglas:
- Respondé en español, claro y corto: primero la respuesta, después el detalle.
- Basate SOLO en las llamadas de abajo. Si no alcanzan para responder, decilo.
- Dá cantidades concretas ("4 de 12 llamadas") y citá casos con el número de llamada (#123).
- No inventes nombres, marcas, precios ni plazos que no estén en los datos.
- Sin markdown pesado: frases y, si hace falta, una lista corta con guiones."""


def _v(x):
    return getattr(x, "value", x)


def bloque(call) -> str:
    t = call.target
    campana = t.campaign.name if t and t.campaign else "?"
    nombre = (t.contact_name or "").split(" ")[0] if t else ""
    # En hora de Paraguay: si no, Claude contesta «a las 13» por una llamada de las 10.
    inicio = call.started_at
    if inicio is not None and inicio.tzinfo is not None:
        inicio = inicio.astimezone(settings.timezone)
    cuando = inicio.strftime("%d/%m/%Y %H:%M") if inicio else "sin fecha"
    a = call.analysis
    cabecera = [f"Llamada #{call.id}", cuando, f"campaña «{campana}»", f"resultado {_v(call.outcome) or 'en curso'}"]
    if nombre:
        cabecera.append(f"cliente {nombre}")
    if a and a.satisfaction_score is not None:
        cabecera.append(f"satisfacción {a.satisfaction_score:.1f}/10")
    if a and a.sentiment:
        cabecera.append(f"sentimiento {a.sentiment}")
    lineas = [" · ".join(cabecera)]
    for r in sorted(call.answers or [], key=lambda r: r.question.position if r.question else 0):
        valor = f" (valor {r.value_numeric:g})" if r.value_numeric is not None else ""
        dijo = (r.transcript or "sin respuesta").strip().replace("\n", " ")[:300]
        pregunta = r.question.text if r.question else "?"
        lineas.append(f"- {pregunta} → «{dijo}»{valor}")
    if a and a.summary:
        lineas.append(f"Resumen: {a.summary}")
    if a and a.followup_reason:
        lineas.append(f"Seguimiento: {a.followup_reason}")
    return "\n".join(lineas)


def contexto(calls) -> tuple[str, int]:
    """El texto para el modelo y cuántas llamadas entraron (las más nuevas primero)."""
    partes, usadas, largo = [], 0, 0
    for c in list(calls)[:MAX_LLAMADAS]:
        b = bloque(c)
        if largo + len(b) > MAX_CARACTERES:
            break
        partes.append(b)
        largo += len(b) + 2
        usadas += 1
    return "\n\n".join(partes), usadas


def preguntar(pregunta: str, calls) -> tuple[str | None, int]:
    """(respuesta o None si Claude no está o falló, llamadas usadas)."""
    texto, usadas = contexto(calls)
    if not usadas:
        return "No hay llamadas con estos filtros para analizar.", 0
    respuesta = llm.complete_sync(
        SISTEMA,
        f"LLAMADAS ({usadas}):\n\n{texto}\n\nPREGUNTA: {pregunta.strip()}",
        max_tokens=900,
    )
    return limpiar(respuesta), usadas


def limpiar(texto: str | None) -> str | None:
    """El modelo a veces marca negritas o títulos con markdown aunque se le pida que no: en la
    página se verían los asteriscos. Se quitan las marcas y queda el texto."""
    if not texto:
        return texto
    lineas = [re.sub(r"^#{1,6}\s*", "", l) for l in texto.replace("**", "").replace("__", "").splitlines()]
    return "\n".join(lineas).strip()
