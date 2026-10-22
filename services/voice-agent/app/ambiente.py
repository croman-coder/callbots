"""Sonido de fondo de la llamada: ambiente de oficina y tecleo, sintetizados acá (sin grabaciones de terceros).

Pedido de Croman (22/10/2026): entre frase y frase la línea quedaba en silencio digital, que suena a máquina; y la
espera mientras el agente busca en el sistema era un silencio raro. Una persona en una oficina tiene ambiente atrás y
teclea mientras busca.

- `oficina()`: ruido de sala con forma de espectro (más grave que agudo, como el aire acondicionado y la sala) más un
  murmullo lejano de voces (ruido en la banda de la voz, modulado al ritmo de las sílabas). Se arma con FFT, así el
  bucle empalma sin corte.
- `teclado()`: tecleo con ritmo de palabras (teclas cada ~0,1 s, una pausa entre palabras y alguna más larga), con
  la barra espaciadora más grave.

Los niveles son bajos a propósito: el ambiente queda unos 30 dB por debajo de la voz del agente (se oye, no molesta
y no le llega a la escucha como si fuera el cliente) y el teclado, unos 18 dB.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np

TASA = 8000
NIVEL_OFICINA = 160.0   # RMS en muestras de 16 bits: ~ −46 dBFS
NIVEL_TECLADO = 450.0   # RMS mientras teclea: ~ −37 dBFS


def _ruido_con_forma(n: int, forma, rng: np.random.Generator) -> np.ndarray:  # noqa: ANN001 - f(hz) -> ganancia
    """Ruido periódico (el final empalma con el principio) con el espectro que dice `forma`."""
    espectro = rng.standard_normal(n // 2 + 1) + 1j * rng.standard_normal(n // 2 + 1)
    espectro *= forma(np.fft.rfftfreq(n, 1 / TASA))
    espectro[0] = 0
    senal = np.fft.irfft(espectro, n)
    return senal / (np.sqrt(np.mean(senal ** 2)) or 1.0)


def _a_bytes(senal: np.ndarray) -> bytes:
    return np.clip(np.round(senal), -32768, 32767).astype(np.int16).tobytes()


@lru_cache(maxsize=1)
def oficina(segundos: int = 24) -> bytes:
    rng = np.random.default_rng(22102026)
    n = TASA * segundos
    # La sala y el aire: cae ~4,5 dB por octava, con un poco más de cuerpo entre 80 y 400 Hz.
    sala = _ruido_con_forma(n, lambda f: (g := np.maximum(f, 20.0)) ** -0.75
                            * (1 + 1.5 * np.exp(-((np.log2(g / 180)) ** 2))), rng)
    # Voces lejanas: banda de la voz (300 a 1.500 Hz), con sílabas (envolvente al azar de 2 a 6 Hz) y frases.
    voces = _ruido_con_forma(n, lambda f: np.exp(-((np.log2(np.maximum(f, 20.0) / 650)) ** 2) / 0.6), rng)
    silabas = _ruido_con_forma(n, lambda f: np.where((f > 2) & (f < 6), 1.0, 0.0), rng)
    frases = _ruido_con_forma(n, lambda f: np.where((f > 0) & (f < 0.35), 1.0, 0.0), rng)
    envolvente = np.clip(silabas, 0, None) * np.clip(frases + 0.3, 0, None)
    voces = voces * envolvente / (np.sqrt(np.mean((voces * envolvente) ** 2)) or 1.0)
    mezcla = sala + 0.45 * voces
    return _a_bytes(mezcla / np.sqrt(np.mean(mezcla ** 2)) * NIVEL_OFICINA)


def _tecla(rng: np.random.Generator, espacio: bool) -> np.ndarray:
    """Una tecla: un clic brillante y corto (el plástico) y un golpe grave más corto todavía (el fondo)."""
    largo = int(TASA * 0.045)
    t = np.arange(largo) / TASA
    clic = rng.standard_normal(largo) * np.exp(-t / (0.006 if not espacio else 0.009))
    clic = np.diff(clic, prepend=0.0)  # filtro pasa altos: suena a plástico, no a soplido
    golpe = np.sin(2 * np.pi * (160 if espacio else 230) * t) * np.exp(-t / 0.008)
    return clic * (0.8 if espacio else 1.0) + golpe * (0.9 if espacio else 0.5)


@lru_cache(maxsize=1)
def teclado(segundos: int = 14) -> bytes:
    rng = np.random.default_rng(1022)
    n = TASA * segundos
    salida = np.zeros(n + TASA)
    t = 0.15
    while t < segundos - 0.2:
        for _ in range(int(rng.integers(3, 9))):  # una palabra
            i = int(t * TASA)
            tecla = _tecla(rng, False) * rng.uniform(0.6, 1.0)
            salida[i:i + len(tecla)] += tecla
            t += rng.uniform(0.07, 0.16)
        i = int(t * TASA)
        espacio = _tecla(rng, True) * rng.uniform(0.8, 1.0)
        salida[i:i + len(espacio)] += espacio
        t += rng.uniform(0.18, 0.35) + (rng.uniform(0.4, 1.0) if rng.random() < 0.15 else 0.0)
    salida = salida[:n]
    activo = salida[np.abs(salida) > 1e-3]
    rms = np.sqrt(np.mean(activo ** 2)) if activo.size else 1.0
    return _a_bytes(salida / rms * NIVEL_TECLADO)
