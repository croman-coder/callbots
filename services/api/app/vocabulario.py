"""Vocabulario del agente: las palabras que Whisper tiene que reconocer en lo que dice el cliente.

Whisper no conoce las marcas ni los modelos: «Renó Kárdian» lo escribía «Renault Cardian», «Zíker» como «seeker» y
«Jota Eme E Ve» como «JMEB». Pasándole la lista en el prompt los escribe bien (medido el 21/10/2026 con la voz por
teléfono a 8 kHz: 8 de 17 nombres sin la lista, 17 de 17 con ella, y los números del cero al diez igual de bien).

La lista de cada agente se suma a las palabras del diccionario de pronunciación (que ya son marcas y siglas). El
prompt de Whisper tiene un tope (unos 220 tokens): se mandan las primeras que entren, las del agente primero.
"""

from __future__ import annotations

import re

MAX_PALABRAS = 100
MAX_PALABRA = 40
# Lo que entra en el prompt de Whisper junto con la frase base (unos 220 tokens en total).
MAX_CARACTERES_PROMPT = 500

MARCAS_SANTA_ROSA = (
    "Santa Rosa", "Renault", "Mitsubishi", "GWM", "Haval", "Jetour", "JAC", "Zeekr", "Leapmotor", "JMEV", "Soueast",
    "Renew",
)


def de_texto(texto: str | None) -> list[str]:
    """Una palabra (o nombre de varias palabras) por línea o separadas por coma; sin repetidas ni vacías."""
    salida: list[str] = []
    vistas: set[str] = set()
    for parte in re.split(r"[\n,;]+", texto or ""):
        palabra = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", parte)).strip()[:MAX_PALABRA]
        if palabra and palabra.lower() not in vistas:
            vistas.add(palabra.lower())
            salida.append(palabra)
    return salida[:MAX_PALABRAS]


def normalizar(texto: str | None) -> str | None:
    """Lo que se guarda: una por línea. Vacío → None."""
    palabras = de_texto(texto)
    return "\n".join(palabras) or None


def para_whisper(vocabulario_agente: str | None, diccionario: list[dict], extra: list[str] | None = None) -> list[str]:
    """Las palabras que viajan al voice-agent: las del agente, las que suma el sistema (`extra`: por ejemplo los
    talleres de la agenda) y después las del diccionario, sin pasarse del tope."""
    todas = de_texto(vocabulario_agente) + list(extra or []) + [str(e.get("palabra") or "") for e in diccionario]
    salida: list[str] = []
    largo = 0
    vistas: set[str] = set()
    for p in todas:
        p = p.strip()
        if not p or p.lower() in vistas:
            continue
        if largo + len(p) + 2 > MAX_CARACTERES_PROMPT:
            break
        vistas.add(p.lower())
        salida.append(p)
        largo += len(p) + 2
    return salida
