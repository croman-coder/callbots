"""Filtros de texto de la transcripción, sin dependencias (se prueban sin la GPU)."""
from __future__ import annotations

from .reglas import normalize

# Lo que el STT le entrega al guion cuando alguien habló y no se pudo sacar nada («no» y «entendió» no pueden pasar por el sí/no de las reglas).
MARCA_INAUDIBLE = "(no se entendió)"

# Cuántas veces seguidas tiene que repetirse un tramo para que sea un bucle de Whisper y no énfasis: «no, no, no, no» lo dice una persona
# enojada (se pide 5 de una palabra sola); «no puedo, no puedo, no puedo» ya no lo dice nadie (3 de un tramo de dos a cuatro palabras).
REPETICIONES_UNA_PALABRA = 5
REPETICIONES_TRAMO = 3


def hay_bucle(texto: str) -> bool:
    """Whisper se quedó repitiendo («…ahora no puedo, no puedo, no puedo, no puedo…»), típico con la pista del saludo en audios cortos."""
    p = normalize(texto).split()
    for n in (1, 2, 3, 4):
        minimo = REPETICIONES_UNA_PALABRA if n == 1 else REPETICIONES_TRAMO
        for i in range(len(p) - n * minimo + 1):
            tramo, j, veces = p[i:i + n], i, 0
            while p[j:j + n] == tramo:
                veces += 1
                j += n
            if veces >= minimo:
                return True
    return False
