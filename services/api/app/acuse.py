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

# Nota baja sin motivo («uno, un desastre»): antes de seguir, se le pregunta qué pasó (reunión del 30/10/2026: «si el
# cliente se queja, primero atender eso y recién después seguir»). Una vez por llamada: el voice-agent lo sabe porque
# alguna de estas ya está en `ya_dichos`.
REPREGUNTAS = ["Uy, lo lamento mucho. ¿Me cuenta qué pasó?", "Entiendo, y lo lamento. ¿Qué fue lo que pasó?"]
# Después de que contó qué pasó, si Claude no llega: muestra que lo escuchó, sin prometer nada.
FIJAS_MOTIVO = ["Entiendo, gracias por contármelo.", "Comprendo, y lamento mucho que haya pasado eso."]

# Cómo está el cliente, para el tono. Palabras sin acentos (ver _palabras); se busca al principio de la palabra.
_ENOJO = re.compile(
    r"\b(pesim|desastre|horrible|terrible|malisim|verguenza|porqueria|indignad|molest|enojad|calentad|hart[oa]s?\b|"
    r"asco|estafa|burla|nunca mas|muy mal\b|re mal\b|mal servicio|mala atencion|una cagada|cualquier cosa)"
)
_ALEGRIA = re.compile(
    r"\b(excelente|genial|buenisim|perfect|content[oa]|feliz|encantad|muy bien\b|re bien\b|espectacular|impecable|"
    r"lo mejor|barbaro|joya|de diez|un lujo|muy amables?)"
)

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
- Tono justo según cómo está el cliente (te lo digo abajo): enojado o disconforme, empatía sincera y calma, sin \
exclamaciones alegres («Lamento mucho que…»); contento, alegría breve («¡Qué bueno que…!»); neutro, agradecé.
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
    escala = _escala(valor, qtype)
    if escala >= SATISFACTORY_MIN:
        return "alta"
    return "media" if escala >= 7 else "baja"


SISTEMA_MOTIVO = """Hacés una encuesta telefónica de satisfacción para la posventa de Santa Rosa, una concesionaria de \
Paraguay. El cliente dio una nota baja y le preguntaste qué pasó. Escribí lo que le decís antes de seguir con la \
encuesta: UNA frase corta (como mucho 18 palabras) que muestre que entendiste su problema concreto.
- Retomá lo que contó en pocas palabras, sin repetirlo textual. Empatía sincera y calma, sin exclamaciones.
- No hagas preguntas. No prometas llamados, soluciones, descuentos ni que alguien se va a comunicar. No pongas \
excusas ni culpes a nadie. No nombres a otras marcas ni empresas.
- De usted, español de Paraguay, natural, como una persona que habla por teléfono.
Respondé solo la frase, sin comillas."""


def emocion(valor: float | None, qtype: QuestionType | str, transcripcion: str) -> str:
    """«enojado», «neutro» o «contento», para el tono del acuse.

    Las palabras mandan («un desastre», «excelente»); sin ellas, el puntaje: 0 a 3 es enojado, 9 o 10 contento. Una nota
    alta con quejas («diez, pero la espera fue terrible») queda neutra: ni festejo ni pésame.
    """
    texto = " ".join(_palabras(transcripcion))
    b = banda(valor, qtype)
    if _ENOJO.search(texto):
        return "neutro" if b == "alta" else "enojado"
    if _ALEGRIA.search(texto) or b == "alta":
        return "contento"
    if b == "baja" and valor is not None and _escala(valor, qtype) <= 3:
        return "enojado"
    return "neutro"


def repreguntar(valor: float | None, qtype: QuestionType | str, transcripcion: str,
                ya_dichos: list[str] | None = None) -> bool:
    """Nota baja sin motivo y todavía no se preguntó en esta llamada: hay que preguntarle qué pasó."""
    if banda(valor, qtype) != "baja" or dijo_algo_mas(transcripcion):
        return False
    return not set(REPREGUNTAS) & set(ya_dichos or [])


def _escala(valor: float, qtype: QuestionType | str) -> float:
    return valor * 2 if QuestionType(qtype) == QuestionType.SCALE_1_5 and valor <= 5 else valor


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
    return [f for lista in FIJAS.values() for f in lista] + REPREGUNTAS + FIJAS_MOTIVO + [CIERRE_DISCONFORME]


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
    """(lo que dice el agente, banda). Nunca levanta: ante cualquier problema, la frase fija. Ver `responder`."""
    respuesta = await responder(pregunta, qtype, transcripcion, valor, posicion, ya_dichos)
    return respuesta["acuse"], respuesta["banda"]


async def responder(pregunta: str, qtype: QuestionType | str, transcripcion: str, valor: float | None,
                    posicion: int, ya_dichos: list[str] | None = None) -> dict:
    """{"acuse", "banda", "emocion", "repregunta"}. Nunca levanta: ante cualquier problema, la frase fija.

    `repregunta`: el acuse termina en «¿Me cuenta qué pasó?» y el voice-agent tiene que escuchar el motivo
    (`acuse_motivo`) antes de la próxima pregunta.
    """
    b = banda(valor, qtype)
    animo = emocion(valor, qtype, transcripcion)
    if repreguntar(valor, qtype, transcripcion, ya_dichos):
        texto = REPREGUNTAS[posicion % len(REPREGUNTAS)]
        return {"acuse": texto, "banda": b, "emocion": animo, "repregunta": True}
    base = fija(valor, qtype, posicion, ya_dichos)
    salida = {"acuse": base, "banda": b, "emocion": animo, "repregunta": False}
    if not llm.is_configured() or not (b == "abierta" or dijo_algo_mas(transcripcion)):
        return salida
    puntaje = f"\n(Puntaje que dio: {valor:g}.)" if valor is not None and b != "abierta" else ""
    pedido = f"Pregunta: «{pregunta}»\nLo que contestó el cliente: «{transcripcion}»{puntaje}\nCómo está: {animo}."
    try:
        texto = await asyncio.wait_for(llm.complete(SISTEMA, pedido, max_tokens=80, tipo="acuse"), timeout=ESPERA_CLAUDE)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001 - del otro lado hay alguien esperando
        log.info("Acuse con Claude no llegó a tiempo (%s): va la frase fija", exc.__class__.__name__)
        return salida
    propia = _limpiar(texto)
    if propia and propia not in set(ya_dichos or []):
        salida["acuse"] = propia
    return salida


async def acuse_motivo(motivo: str, posicion: int, ya_dichos: list[str] | None = None) -> str:
    """Lo que dice el agente después de que el cliente contó qué pasó. Nunca levanta."""
    dichos = set(ya_dichos or [])
    base = next((f for f in FIJAS_MOTIVO[posicion % 2:] + FIJAS_MOTIVO if f not in dichos), FIJAS_MOTIVO[0])
    if not llm.is_configured() or len(_palabras(motivo)) < 2:
        return base
    try:
        texto = await asyncio.wait_for(
            llm.complete(SISTEMA_MOTIVO, f"Lo que contó el cliente: «{motivo}»", max_tokens=90, tipo="motivo"), timeout=ESPERA_CLAUDE)
    except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
        log.info("Acuse del motivo con Claude no llegó a tiempo (%s): va la frase fija", exc.__class__.__name__)
        return base
    propia = _limpiar(texto)
    return propia if propia and propia not in dichos else base
