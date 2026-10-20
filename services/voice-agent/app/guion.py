"""Texto del guion que no depende de audio ni de modelos (se prueba sin instalar nada pesado)."""

from __future__ import annotations

import re


def personalizar(text: str, contact_name: str | None) -> str:
    """Reemplaza {nombre} en el guion. Sin nombre, deja la frase natural.

    La API calcula lo mismo (`con_nombre` en routers/internal.py) para precalentar el audio de cada
    saludo: tienen que dar el texto idéntico, porque el audio se busca por el texto exacto.
    """
    if "{nombre}" not in text:
        return text
    if contact_name:
        return text.replace("{nombre}", contact_name)
    # Sin nombre: saca el placeholder con la coma o el espacio que lo precede
    # ("¡Hola, {nombre}!" -> "¡Hola!", "Hola {nombre}, le hablamos" -> "Hola, le hablamos").
    return re.sub(r",?[ ]*\{nombre\}", "", text).replace("  ", " ")


# Un texto largo dicho de corrido suena a locutor sin pausa: cada frase se dice por separado
# y el audio las une con un silencio.
_FIN_DE_FRASE = re.compile(r"(?<=[.!?…])\s+")
# Una frase más corta que esto ("¡Hola, Marta!", "Muy bien, gracias.", "Sr.") se pega a la
# siguiente: sola sonaría cortada y no vale una pausa.
MIN_CARACTERES_FRASE = 15


def partir_frases(text: str) -> list[str]:
    """Parte un texto en frases para decirlas con una pausa entre una y otra.

    Pasarle una frase ya partida devuelve la misma frase: `tts` cachea el audio por frase y
    `warm_up` vuelve a partir lo que le llega.
    """
    frases = [f.strip() for f in _FIN_DE_FRASE.split((text or "").strip()) if f.strip()]
    salida: list[str] = []
    pendiente = ""
    for frase in frases:
        if pendiente:
            frase = f"{pendiente} {frase}"
            pendiente = ""
        if len(frase) < MIN_CARACTERES_FRASE:
            pendiente = frase
        else:
            salida.append(frase)
    if pendiente:
        if salida:
            salida[-1] = f"{salida[-1]} {pendiente}"
        else:
            salida.append(pendiente)
    return salida
