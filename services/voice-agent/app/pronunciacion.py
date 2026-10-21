"""Diccionario de pronunciación: cómo tiene que decir el bot las marcas y las siglas.

Un sintetizador en español lee «Renault» a la española y «GWM» como una palabra. Cada entrada dice cómo se escribe
(`palabra`) y cómo se dice (`dicho`, escrito como se lee). El reemplazo se hace solo al generar el audio: el guion y
la transcripción siguen con la palabra original. Es la misma regla que usa el panel de la API (`app/pronunciaciones.py`)
para dejar escuchar el resultado; los casos de prueba de las dos copias son los mismos.
"""

from __future__ import annotations

import re


def aplicar(texto: str, entradas: list[dict]) -> str:
    """Reemplaza cada palabra por cómo se dice: palabra completa, sin importar mayúsculas, en una sola pasada (lo que
    se escribió como «dicho» no vuelve a reemplazarse). Las frases más largas ganan: «Santa Rosa» antes que «Santa»."""
    mapa = {str(e["palabra"]).lower(): str(e["dicho"]) for e in entradas if e.get("palabra") and e.get("dicho")}
    if not mapa or not texto:
        return texto
    claves = sorted(mapa, key=len, reverse=True)
    patron = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(c) for c in claves) + r")(?!\w)", re.IGNORECASE)
    return patron.sub(lambda m: mapa[m.group(0).lower()], texto)
