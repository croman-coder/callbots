"""¿El cliente pidió hablar con una persona?

Reglas, sin LLM: pasa en medio de la llamada y cada segundo de espera se nota. Se mira la
transcripción del cliente en cada respuesta, pero solo se usa si el agente tiene un teléfono de
asesor cargado.

Tiene que ser conservador. Pasar a un asesor una llamada que no lo pidió es peor que no pasar una
que sí: el cliente puede repetirlo y el bot lo entiende la segunda vez. Por eso se exige un verbo de
pedido ("quiero", "pásame"…) o la construcción «hablar con» junto a la persona.
"""

from __future__ import annotations

import re
import unicodedata

# Quién se pide. Raíces, para que entren los géneros y los plurales.
_PERSONA = r"(asesor|ejecutiv|persona|alguien|humano|encargad|supervisor|vendedor|gerente|agente|operador|recepcion|gente)"

_PATRONES = [
    # «quiero hablar con un asesor», «necesito hablar con una persona»
    rf"\bhabl(ar|arle|arme|ame|eme)\s+con\s+(un|una|el|la|alguien|algun)?\s*\w{{0,12}}\s*{_PERSONA}",
    # «pásame con un asesor», «comuníqueme con una persona», «me pasa con alguien»
    rf"\b(pas|comunic|comuniqu|deriv|transfier|transfir|conect)\w{{0,6}}\s+(me\s+)?con\s+(un|una|el|la|alguien)?\s*\w{{0,12}}\s*{_PERSONA}",
    # «prefiero una persona», «quiero un asesor», «necesito un humano»
    rf"\b(quiero|quisiera|necesito|prefiero|preferiria|me gustaria)\s+(que\s+)?(me\s+)?(atienda\s+|atiendan\s+|habl\w+\s+)?(con\s+)?(un|una|el|la|alguien)\s*\w{{0,12}}\s*{_PERSONA}",
    # «no quiero hablar con una máquina / un robot / una grabación»
    r"\bno\s+(quiero|me gusta|pienso)\s+(hablar\s+)?con\s+(una?\s+)?(maquina|robot|grabacion|computadora|inteligencia)",
    # «¿es una persona?»: no, eso es una pregunta, no un pedido. Se deja afuera a propósito.
]
_RE = [re.compile(p) for p in _PATRONES]


def _plano(texto: str) -> str:
    """Minúsculas y sin tildes: Whisper a veces las pone y a veces no."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9ñ\s]", " ", sin_tildes)


def pide_persona(texto: str | None) -> bool:
    if not texto or len(texto.strip()) < 6:
        return False
    plano = re.sub(r"\s+", " ", _plano(texto)).strip()
    return any(r.search(plano) for r in _RE)
