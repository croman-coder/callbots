"""Lo que dice el agente cuando el cliente contesta el «¿tiene un minutito?» del saludo.

Antes, si decía que sí, el agente largaba la primera pregunta sin más: «Sí, tengo.» → «Primero, ¿qué tan satisfecho
quedó…?» (prueba del 3/11/2026). Ahora se le agradece, igual que después de cada respuesta (ver acuse.py):

- Si dijo solo que sí («sí, tengo», «dale», «claro»), una frase fija: sale al instante y del caché del TTS.
- Si contó algo más («sí, pero rápido que estoy en el trabajo»), Claude escribe una frase corta que lo retoma, con
  tope de tiempo: si tarda, va la fija.

Y un «no» a secas o «no, gracias» es un «ahora no»: se corta amable y se lo llama otro día. Antes seguía con la
encuesta como si hubiera dicho que sí.
"""

from __future__ import annotations

import asyncio
import logging

from app.acuse import _limpiar, _palabras
from app.services import llm

log = logging.getLogger(__name__)

# Igual que en acuse.py: Haiku contesta una frase en ~0,6 a 1 s; más que esto ya es un silencio raro.
ESPERA_CLAUDE = 1.6

FIJAS = ["¡Perfecto, muchas gracias!", "¡Muchas gracias! Va a ser rapidito.", "¡Buenísimo, gracias!"]

_NEGACIONES = {"no", "nop", "nah", "nel"}
# Lo que, junto a un «no», lo vuelve un sí o una duda: «no hay problema», «no, dale», «no, todo bien», «no sé».
_NO_QUE_NO_ES_NO = {
    "si", "dale", "claro", "bueno", "ok", "okay", "problema", "drama", "adelante", "gusto", "bien", "pregunte",
    "diga", "digame", "se", "nomas", "listo",
}
# Las palabras de un «sí» simple: si no dijo más que estas, alcanza la frase fija.
_DE_UN_SI = {
    "si", "sii", "tengo", "dale", "claro", "bueno", "ok", "okay", "hola", "como", "no", "digame", "diga", "adelante",
    "por", "supuesto", "nomas", "gusto", "con", "eh", "este", "mmm", "buenas", "buen", "tardes", "dias", "noches",
    "listo", "va", "pues", "que", "tal", "un", "minuto", "minutito", "momento", "senor", "senora", "perfecto",
    "seguro", "a", "ver", "ah", "de", "acuerdo", "pregunte", "ya", "obvio", "bien", "esta", "todo", "gracias", "muchas",
    "vamos",
    # Fórmulas de cortesía que son un sí: «no hay problema», «no hay drama», «a la orden», «con mucho gusto», «dele».
    "hay", "problema", "drama", "la", "orden", "mucho", "dele", "vale",
}

SISTEMA = """Hacés una encuesta telefónica corta para Santa Rosa, una concesionaria de Paraguay. Le preguntaste al \
cliente si tenía un minuto y aceptó. Escribí lo que le decís justo antes de la primera pregunta: UNA frase corta \
(como mucho 12 palabras) que le agradezca y retome lo que dijo.
- Si está apurado u ocupado, agradecé el tiempo y decí que va a ser rápido. Si dijo algo amable, devolvé la amabilidad.
- No hagas preguntas ni empieces la encuesta. No prometas llamados, soluciones ni descuentos. No saludes ni te despidas.
- De usted, español de Paraguay, natural, como una persona que habla por teléfono.
Respondé solo la frase, sin comillas."""


def es_negativa(texto: str) -> bool:
    """Contestó que no al minuto («No, gracias.», «No.», «Nop»): no es una baja, es un «ahora no»."""
    palabras = _palabras(texto)
    if not palabras or palabras[0] not in _NEGACIONES:
        return False
    return not set(palabras) & _NO_QUE_NO_ES_NO


def dijo_algo_mas(texto: str) -> bool:
    """Además del sí contó algo («sí, pero rápido que estoy en el trabajo»): ahí vale la pena que Claude lo retome."""
    return len([p for p in _palabras(texto) if p not in _DE_UN_SI]) >= 2


def fija(indice: int) -> str:
    return FIJAS[indice % len(FIJAS)]


async def acuse(texto: str, indice: int = 0) -> str:
    """La frase que dice el agente después del sí. Nunca levanta: ante cualquier problema, la fija."""
    base = fija(indice)
    if not llm.is_configured() or not dijo_algo_mas(texto):
        return base
    try:
        respuesta = await asyncio.wait_for(
            llm.complete(SISTEMA, f"Lo que contestó el cliente: «{texto}»", max_tokens=60, tipo="acuse_saludo"),
            timeout=ESPERA_CLAUDE,
        )
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - del otro lado hay alguien esperando
        log.info("Acuse del saludo con Claude no llegó a tiempo (%s): va la frase fija", exc.__class__.__name__)
        return base
    return _limpiar(respuesta) or base
