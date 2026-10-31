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

# Silencio entre una frase y la siguiente, según cómo termina (ms). El audio de cada frase ya trae ~110 ms de bordes
# (tts.ajustar_ritmo): entre frases quedan ~0,5 s. Antes eran 450 ms fijos más los bordes sin recortar de la voz
# clonada (~310 ms): ~760 ms de hueco y la charla sonaba lenta (reunión del 30/10/2026).
PAUSA_ENTRE_FRASES_MS = 380
PAUSA_TRAS_PREGUNTA_MS = 420
PAUSA_TRAS_SUSPENSIVOS_MS = 550


def pausa_tras(frase: str) -> int:
    """Cuánto silencio va después de esta frase si sigue otra."""
    final = (frase or "").rstrip()
    if final.endswith(("…", "...")):
        return PAUSA_TRAS_SUSPENSIVOS_MS
    if final.endswith("?"):
        return PAUSA_TRAS_PREGUNTA_MS
    return PAUSA_ENTRE_FRASES_MS


# Ritmo de una voz clonada de una persona (OmniVoice en VoiceStudio). El modelo estima cuánto dura la frase y, si le
# sobra tiempo, lo llena con pausas al azar: el 31/10/2026, con la voz de Agustín, 1,2 s después de «Del cero al
# diez,» y ninguna después de «Entiendo,» en otra pasada. Se le pide la duración: medido en cinco frases, a 15 letras/s
# articula a ~18,5 letras/s (como las voces IA) y las pausas quedan en 0,25 s o menos; a 13 todavía sobraban. Las voces
# IA de Callbot no la necesitan (tts._es_voz_ia). La velocidad del agente se aplica después, al audio
# (tts.cambiar_velocidad).
LETRAS_POR_SEGUNDO = 15.0
PAUSA_COMA_S = 0.25
PAUSA_PUNTO_S = 0.3
_PAUSA_CORTA = re.compile(r"[,;:]")
_FIN = re.compile(r"[.!?…]+")


def duracion_estimada(texto: str) -> float:
    """Segundos que debería durar el texto dicho con calma: letras a LETRAS_POR_SEGUNDO más una pausa por coma y por
    punto seguido."""
    letras = len(re.findall(r"[^\W\d_]|\d", texto or ""))
    if not letras:
        return 0.0
    puntos = max(len(_FIN.findall(texto)) - 1, 0)
    segundos = letras / LETRAS_POR_SEGUNDO + PAUSA_COMA_S * len(_PAUSA_CORTA.findall(texto)) + PAUSA_PUNTO_S * puntos
    return round(segundos + 0.15, 2)


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
