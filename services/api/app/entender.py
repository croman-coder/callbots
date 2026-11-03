"""Qué nota dio el cliente cuando las reglas no lo entienden: Claude lo interpreta, con tope de tiempo.

Las reglas (services/scoring.py) contestan al instante y entienden lo común: un número, «excelente», «un desastre». Lo
que se les escapa («Lo peor que me pasó en la vida», «Ni loco lo recomiendo») antes era «no se entendió»: el agente
volvía a preguntar y, al segundo intento, la respuesta se perdía (prueba del 3/11/2026). Ahora, si las reglas no
entienden, Claude decide la nota. Si la respuesta es ambigua («no tan satisfecho», «más o menos») o no contesta la
pregunta, Claude no inventa un número: devuelve null y se vuelve a preguntar como siempre.

El voice-agent pide a la vez el acuse (/acuse) y guardar la respuesta (/answers): los dos tienen que ver la misma nota,
así que lo que contesta Claude se recuerda un rato por sesión, pregunta y transcripción, y se le pregunta una sola vez.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.models import QuestionType
from app.services import llm
from app.services.scoring import Interpretation, calificacion_negada, interpret

log = logging.getLogger(__name__)

# Haiku contesta un JSON corto en ~1 s. Mientras tanto el cliente espera en silencio: más que esto, se repregunta.
ESPERA_CLAUDE = 1.8
# Cuánto se recuerda lo que contestó Claude (el acuse y la respuesta llegan con milisegundos de diferencia).
VIGENCIA_SEGUNDOS = 120.0

_RANGOS = {
    QuestionType.SCALE_1_10: (0, 10, "La escala es del 0 al 10: 0 es lo peor y 10 lo mejor."),
    QuestionType.SCALE_1_5: (1, 5, "La escala es del 1 al 5: 1 es lo peor y 5 lo mejor."),
    QuestionType.YES_NO: (0, 1, "Es una pregunta de sí o no: sí es 1 y no es 0."),
    QuestionType.NUMERIC: (0, 99, "Se contesta con un número de 0 a 99."),
}

SISTEMA = """Sos parte de una encuesta telefónica de satisfacción de Santa Rosa, una concesionaria de Paraguay. El \
cliente contestó una pregunta y hay que anotar su respuesta. Devolvé SOLO un JSON: {"valor": número o null}.
- Si dijo un valor de la escala, ese.
- Si no lo dijo pero su respuesta es clara y está en un extremo, el extremo: «un desastre», «pésimo», «lo peor», «ni \
loco lo recomiendo» → el mínimo; «excelente», «impecable», «de diez» → el máximo.
- Si es intermedia o ambigua («no tan satisfecho», «más o menos», «normal»), si no contesta la pregunta (pregunta algo, \
habla de otra cosa, no se entiende) o si nombra varios números sin elegir uno: null. No adivines: con null se le \
vuelve a preguntar."""

# (sesión, pregunta, transcripción) → (cuándo, tarea de Claude)
_en_curso: dict[tuple, tuple[float, asyncio.Task]] = {}


def _vale_preguntarle(transcript: str, qtype: QuestionType, reglas: Interpretation) -> bool:
    """Claude solo cuando las reglas no entendieron algo que sí es una respuesta, y hay una escala que anotar."""
    if reglas.understood or reglas.is_optout or reglas.is_silence or qtype not in _RANGOS:
        return False
    # «No tan satisfecho»: no hay número que adivinar, se le pregunta (y no se gasta un segundo en Claude).
    return llm.is_configured() and not calificacion_negada(transcript)


def _valor(texto: str | None, qtype: QuestionType) -> float | None:
    datos = llm.parse_json_object(texto) or {}
    valor = datos.get("valor")
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    minimo, maximo, _ = _RANGOS[qtype]
    if not minimo <= valor <= maximo or (qtype is QuestionType.YES_NO and valor not in (0, 1)):
        return None
    return float(valor)


async def _preguntar(transcript: str, qtype: QuestionType, pregunta: str) -> float | None:
    sistema = f"{SISTEMA}\n- {_RANGOS[qtype][2]}"
    texto = await llm.complete(sistema, f"Pregunta: «{pregunta}»\nLo que contestó: «{transcript}»", max_tokens=30,
                               tipo="entender")
    return _valor(texto, qtype)


def _resultado(tarea: asyncio.Task) -> float | None:
    if tarea.cancelled() or tarea.exception() is not None:
        return None
    return tarea.result()


async def _de_claude(transcript: str, qtype: QuestionType, pregunta: str, clave: tuple | None) -> float | None:
    loop = asyncio.get_running_loop()
    ahora = time.monotonic()
    for k, (cuando, _) in list(_en_curso.items()):
        if ahora - cuando > VIGENCIA_SEGUNDOS:
            del _en_curso[k]
    previo = _en_curso.get(clave) if clave else None
    if previo and previo[1].done():
        return _resultado(previo[1])
    if previo and previo[1].get_loop() is loop:
        tarea = previo[1]
    else:
        tarea = loop.create_task(_preguntar(transcript, qtype, pregunta))
        if clave:
            _en_curso[clave] = (ahora, tarea)
    try:
        return await asyncio.wait_for(asyncio.shield(tarea), timeout=ESPERA_CLAUDE)
    except asyncio.TimeoutError:
        log.info("Claude no llegó a entender la respuesta a tiempo: se vuelve a preguntar")
    except Exception as exc:  # noqa: BLE001 - del otro lado hay alguien esperando
        log.warning("Claude falló entendiendo la respuesta (%s): se vuelve a preguntar", exc.__class__.__name__)
    return None


async def entender(transcript: str | None, qtype: QuestionType | str, pregunta: str = "",
                   clave: tuple | None = None) -> Interpretation:
    """Las reglas y, si no alcanzan, Claude. `clave` (sesión, pregunta, transcripción) hace que el acuse y la respuesta
    compartan la misma consulta. Nunca levanta."""
    qtype = QuestionType(qtype)
    reglas = interpret(transcript, qtype)
    if not _vale_preguntarle(transcript or "", qtype, reglas):
        return reglas
    valor = await _de_claude((transcript or "").strip(), qtype, pregunta, clave)
    if valor is None:
        return reglas
    log.info("Claude entendió %r como %g", (transcript or "")[:60], valor)
    return Interpretation(understood=True, value=valor, source="claude")
