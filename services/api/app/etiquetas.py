"""Cómo se nombran en pantalla y en el Excel los valores internos (en inglés en la base).

Una sola fuente: si el panel dice «no atendió» y el Excel «No answer», alguien termina preguntando
si son lo mismo.
"""

from __future__ import annotations

# Resultado de una llamada (CallOutcome).
RESULTADO = {
    "completed": "completada",
    "partial": "parcial",
    "no_answer": "no atendió",
    "busy": "ocupado",
    "rejected": "rechazada",
    "failed": "error técnico",
    "voicemail": "buzón de voz",
    "transferred": "transferida",
}

# Estado de un destinatario en la cola (TargetStatus): habla de la persona, no de una llamada.
DESTINATARIO = {
    "pending": "esperando",
    "scheduled": "agendado",
    "queued": "en cola",
    "calling": "llamando",
    "completed": "encuestado",
    "no_answer": "sin respuesta",
    "failed": "error técnico",
    "opted_out": "no quiere llamadas",
    "skipped": "descartado",
}

# Causa de corte que informa Asterisk.
CORTE = {
    "normal": "normal",
    "no_answer": "no atendió",
    "busy": "ocupado",
    "rejected": "rechazada",
    "congestion": "red saturada",
    "failed": "error técnico",
    # Lo que reporta el voice-agent cuando él decide cortar (ver dialog.py).
    "buzon_colgo": "buzón de voz, cortó",
    "buzon_mensaje": "buzón de voz, dejó mensaje",
    "transferida": "transferida a un asesor",
    "transferencia_fallida": "no se pudo transferir",
}

_TABLAS = {"resultado": RESULTADO, "destinatario": DESTINATARIO, "corte": CORTE}


def etiqueta(valor, tipo: str = "resultado") -> str:
    """Texto en español de un valor interno. Lo desconocido se muestra tal cual, nunca vacío."""
    if valor is None or valor == "":
        return ""
    clave = str(getattr(valor, "value", valor))
    return _TABLAS[tipo].get(clave, clave.replace("_", " "))


def con_mayuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:]
