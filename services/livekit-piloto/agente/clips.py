"""Las muletillas como archivos de audio chicos: se sintetizan una vez con la voz del agente y se acondicionan (sin dependencias de LiveKit)."""
from __future__ import annotations

import io
import wave

import numpy as np

UMBRAL = 0.02  # por debajo de esto es silencio de la síntesis
MARGEN_S = 0.03
FUNDIDO_ENTRADA_S = 0.005
FUNDIDO_SALIDA_S = 0.04
PICO = 0.7


def acondicionar(x: np.ndarray, sr: int) -> np.ndarray:
    """Recorta el silencio de los lados, suaviza el principio y el final (sin clics) y lleva el pico a 0,7."""
    x = x.astype(np.float32)
    activos = np.where(np.abs(x) > UMBRAL)[0]
    if activos.size == 0:
        return x[:0]
    margen = int(sr * MARGEN_S)
    x = x[max(0, activos[0] - margen): activos[-1] + margen + 1].copy()
    n_in, n_out = min(len(x), int(sr * FUNDIDO_ENTRADA_S)), min(len(x), int(sr * FUNDIDO_SALIDA_S))
    if n_in:
        x[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)
    if n_out:
        x[-n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)
    pico = float(np.abs(x).max())
    return x * (PICO / pico) if pico > 0 else x


def leer_wav(datos: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(datos), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
        return x, w.getframerate()


def escribir_wav(ruta: str, x: np.ndarray, sr: int) -> None:
    with wave.open(ruta, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
