"""Cuándo meter una muletilla: reglas de tiempo, sin LiveKit (se prueban sin la GPU).

Dos casos, que en una charla de verdad se dan distinto:
  * mientras la persona habla largo (un reclamo, el comentario final) se le dice un «claro» o «entiendo» en una pausa corta, sin sacarle el
    turno: `Racha` decide cuándo, con los silencios que ve un segundo VAD más sensible que el del turno;
  * mientras el agente piensa y la respuesta tarda, un «a ver» o «un momento» para no dejar el aire muerto: `Espaciador` evita que se
    repita seguido.
"""
from __future__ import annotations

import math


class Espaciador:
    """Deja pasar un evento como mucho cada `cada` segundos y, si se pide, un máximo de `maximo` veces."""

    def __init__(self, cada: float, maximo: int | None = None) -> None:
        self.cada, self.maximo = cada, maximo
        self._ultimo, self._veces = -math.inf, 0

    def puede(self, t: float) -> bool:
        return t - self._ultimo >= self.cada and (self.maximo is None or self._veces < self.maximo)

    def marcar(self, t: float) -> None:
        self._ultimo, self._veces = t, self._veces + 1


class Racha:
    """Sigue cuánto lleva hablando la persona de corrido y dice cuándo toca un «claro».

    Una racha es habla con pausas menores a `pausa_nueva` segundos; una pausa más larga es una frase nueva. Solo se interviene en una pausa
    corta (el segundo VAD marca el fin de voz a los ~0,2 s de silencio) de una racha que ya lleva `desde` segundos, no más seguido que
    `cada` y nunca con el agente hablando.
    """

    def __init__(self, desde: float = 3.0, cada: float = 7.0, pausa_nueva: float = 0.8) -> None:
        self.desde, self.pausa_nueva = desde, pausa_nueva
        self._esp = Espaciador(cada)
        self._inicio, self._fin = 0.0, -math.inf

    def inicio_de_voz(self, t: float) -> None:
        if t - self._fin >= self.pausa_nueva:
            self._inicio = t

    def fin_de_voz(self, t: float, agente_escuchando: bool) -> bool:
        self._fin = t
        if not agente_escuchando or t - self._inicio < self.desde or not self._esp.puede(t):
            return False
        self._esp.marcar(t)
        return True
