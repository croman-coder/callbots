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
