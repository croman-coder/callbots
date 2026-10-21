"""Diccionario de pronunciación: cómo tiene que decir el bot las palabras que un sintetizador en español lee mal.

Las marcas son lo que más delata a un bot en una concesionaria: «Renault» leído a la española, «GWM» como una
palabra, «Zeekr» como si fuera castellano. Cada entrada dice cómo se escribe (`palabra`) y cómo se dice
(`dicho`, escrito como se lee: «Renó», «Ge Doble Ve Eme»). El cambio se hace solo al generar el audio: el guion, la
transcripción y lo que se ve en el panel siguen con la palabra original.

La misma regla de reemplazo vive en el voice-agent (`app/pronunciacion.py`): el panel la usa para escuchar lo que
va a decir el bot, y el voice-agent para decirlo. Los casos de prueba de las dos copias son los mismos.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Pronunciation

MAX_ENTRADAS = 200
MAX_PALABRA = 60
MAX_DICHO = 120


class PronunciacionError(ValueError):
    """El motivo se puede mostrar tal cual."""


def _limpio(texto: object, maximo: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(texto or ""))).strip()[:maximo]


def validar(palabra: object, dicho: object) -> tuple[str, str]:
    p, d = _limpio(palabra, MAX_PALABRA), _limpio(dicho, MAX_DICHO)
    if not p:
        raise PronunciacionError("Escribí la palabra tal como aparece en el guion.")
    if not d:
        raise PronunciacionError("Escribí cómo se dice, tal como se lee en voz alta.")
    if p == d:
        raise PronunciacionError("Eso se dice igual que se escribe: escribí cómo suena.")
    return p, d


def aplicar(texto: str, entradas: list[dict]) -> str:
    """Reemplaza cada palabra por cómo se dice: palabra completa, sin importar mayúsculas, en una sola pasada (lo que
    se escribió como «dicho» no vuelve a reemplazarse). Las frases más largas ganan: «Santa Rosa» antes que «Santa»."""
    mapa = {str(e["palabra"]).lower(): str(e["dicho"]) for e in entradas if e.get("palabra") and e.get("dicho")}
    if not mapa or not texto:
        return texto
    claves = sorted(mapa, key=len, reverse=True)
    patron = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(c) for c in claves) + r")(?!\w)", re.IGNORECASE)
    return patron.sub(lambda m: mapa[m.group(0).lower()], texto)


def lista(db: Session) -> list[dict]:
    """Las entradas para el agente de voz y para el panel: [{id, palabra, dicho}], por orden alfabético."""
    filas = db.scalars(select(Pronunciation).order_by(func.lower(Pronunciation.palabra))).all()
    return [{"id": f.id, "palabra": f.palabra, "dicho": f.dicho} for f in filas]
