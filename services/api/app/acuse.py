"""Escucha activa en la encuesta: lo que el agente dice después de cada respuesta, antes de la próxima pregunta.

Antes cada pregunta traía su propia transición fija («Muy bien, gracias. ¿Y con la atención…?», «Perfecto. ¿Y con el
plazo…?»), y se decía igual con un 1 que con un 10: el cliente decía «uno, muy mal» y el agente le contestaba «muy
bien, gracias». Ahora el acuse depende de lo que contestó:

- Si dijo solo el puntaje, una frase fija según cómo le fue (alegría breve, agradecimiento o empatía). Sale al
  instante y del caché del TTS.
- Si dijo algo más («uno, me devolvieron el auto sucio») o la pregunta es abierta, Claude escribe una frase corta
  que retoma lo que dijo, con tope de tiempo: si tarda, va la fija.

La transición fija de la pregunta siguiente se saca (`sin_acuse`) cuando ya se dijo un acuse.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata

from app.models import QuestionType
from app.services import llm
from app.services.scoring import SATISFACTORY_MIN

log = logging.getLogger(__name__)

# Cuánto se espera a Claude. Haiku contesta una frase en ~0,6 a 1 s; más que esto ya es un silencio raro.
ESPERA_CLAUDE = 1.6

FIJAS = {
    "alta": ["¡Qué bueno escuchar eso!", "¡Me alegra mucho!", "¡Excelente, muchas gracias!"],
    "media": ["Entiendo, gracias por decírmelo.", "Bien, lo anoto.", "De acuerdo, gracias."],
    "baja": ["Uy, lamento mucho escuchar eso.", "Lo siento mucho, de verdad. Lo vamos a tener en cuenta.",
             "Entiendo, y lo lamento. Gracias por decírmelo con franqueza."],
    "neutra": ["Muy bien, gracias.", "Perfecto, gracias.", "Bien, gracias."],
    "abierta": ["Gracias por contármelo, lo anoto.", "Entiendo, gracias por su sinceridad."],
}

# Antes de la despedida si alguna respuesta fue mala (la dice el voice-agent: FRASE_CIERRE_DISCONFORME, mismo texto).
CIERRE_DISCONFORME = "Lamento que la experiencia no haya sido la que esperaba. Le paso sus comentarios al equipo de Calidad."

# Las transiciones que el autor de la encuesta escribió al principio de las preguntas.
_TRANSICION = re.compile(
    r"^\s*¡?\s*(?:(?:muy\s+)?bien|perfecto|excelente|genial|b[aá]rbaro|buen[ií]simo|de\s+acuerdo|entendido|listo|"
    r"ok(?:ay)?|dale|claro|(?:muchas\s+)?gracias)(?:\s*,?\s*(?:muchas\s+)?gracias)?\s*[.!]+\s*",
    re.IGNORECASE,
)

SISTEMA = """Hacés una encuesta telefónica de satisfacción para la posventa de Santa Rosa, una concesionaria de \
Paraguay. El cliente acaba de contestar una pregunta. Escribí lo que le decís antes de pasar a la siguiente: UNA \
frase corta (como mucho 14 palabras) que muestre que lo escuchaste de verdad.
- Retomá en pocas palabras lo que dijo, sin repetirlo textual y sin repetir el número.
- Tono justo: si está disconforme, empatía sincera («Lamento mucho que…»); si está contento, alegría breve («¡Qué \
bueno que…!»); si es neutro, agradecé.
- No hagas preguntas. No prometas llamados, soluciones, descuentos ni que alguien se va a comunicar. No pongas \
excusas ni culpes a nadie. No saludes ni te despidas.
- De usted, español de Paraguay, natural, como una persona que habla por teléfono.
Respondé solo la frase, sin comillas."""

_PALABRAS_DE_PUNTAJE = {
    "cero", "uno", "un", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "si", "no",
    "de", "del", "le", "pongo", "doy", "daria", "un", "la", "el", "y", "medio", "punto", "puntos", "nota", "sobre",
    "a", "ver", "eh", "este", "bueno", "mmm", "creo", "que", "mas", "o", "menos",
}


def _palabras(texto: str) -> list[str]:
    plano = unicodedata.normalize("NFKD", (texto or "").lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.findall(r"[a-z]+|\d+", plano)


def dijo_algo_mas(transcripcion: str) -> bool:
    """Si además del puntaje contó algo («uno, me lo devolvieron sucio»): ahí vale la pena que Claude lo retome."""
    resto = [p for p in _palabras(transcripcion) if p not in _PALABRAS_DE_PUNTAJE and not p.isdigit()]
    return len(resto) >= 3


def banda(valor: float | None, qtype: QuestionType | str) -> str:
    """Cómo le fue según lo que contestó. La empresa cuenta como satisfactorio solo 9 o 10 (scoring.py)."""
    qtype = QuestionType(qtype)
    if qtype == QuestionType.OPEN:
        return "abierta"
    if valor is None or qtype not in (QuestionType.SCALE_1_10, QuestionType.SCALE_1_5):
        return "neutra"
    escala = valor * 2 if qtype == QuestionType.SCALE_1_5 and valor <= 5 else valor
    if escala >= SATISFACTORY_MIN:
        return "alta"
    return "media" if escala >= 7 else "baja"


def fija(valor: float | None, qtype: QuestionType | str, posicion: int, ya_dichos: list[str] | None = None) -> str:
    """La frase fija de la banda. La posición rota cuál; si esa ya se dijo en la llamada, la siguiente que no. Pasaba
    que P2 y P5 caían en la misma y el agente repetía «Entiendo, y lo lamento…» dos veces (22/10/2026)."""
    opciones = FIJAS[banda(valor, qtype)]
    dichos = set(ya_dichos or [])
    for i in range(len(opciones)):
        candidata = opciones[(posicion + i) % len(opciones)]
        if candidata not in dichos:
            return candidata
    return opciones[posicion % len(opciones)]  # las dijo todas: se repite, no hay de dónde sacar otra


def todas_las_fijas() -> list[str]:
    return [f for lista in FIJAS.values() for f in lista] + [CIERRE_DISCONFORME]


def sin_acuse(texto: str) -> str:
    """La pregunta sin la transición fija del principio («Muy bien, gracias. ¿Y con…?» → «¿Y con…?»)."""
    salida = texto or ""
    while True:
        recortado = _TRANSICION.sub("", salida, count=1)
        if recortado == salida or not recortado.strip():
            break
        salida = recortado
    return salida.strip() or (texto or "")


def _limpiar(texto: str | None) -> str:
    linea = (texto or "").strip().splitlines()[0] if (texto or "").strip() else ""
    linea = linea.strip().strip("«»\"'").strip()
    if not linea or "?" in linea or len(linea) > 160:
        return ""
    return linea


async def acuse(pregunta: str, qtype: QuestionType | str, transcripcion: str, valor: float | None,
                posicion: int, ya_dichos: list[str] | None = None) -> tuple[str, str]:
    """(lo que dice el agente, banda). Nunca levanta: ante cualquier problema, la frase fija."""
    b = banda(valor, qtype)
    base = fija(valor, qtype, posicion, ya_dichos)
    if not llm.is_configured() or not (b == "abierta" or dijo_algo_mas(transcripcion)):
        return base, b
    puntaje = f"\n(Puntaje que dio: {valor:g}.)" if valor is not None and b != "abierta" else ""
    pedido = f"Pregunta: «{pregunta}»\nLo que contestó el cliente: «{transcripcion}»{puntaje}"
    try:
        texto = await asyncio.wait_for(llm.complete(SISTEMA, pedido, max_tokens=80), timeout=ESPERA_CLAUDE)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - del otro lado hay alguien esperando
        log.info("Acuse con Claude no llegó a tiempo (%s): va la frase fija", exc.__class__.__name__)
        return base, b
    propia = _limpiar(texto)
    return (propia if propia and propia not in set(ya_dichos or []) else base), b
