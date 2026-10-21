"""Whisper queda caliente al arrancar: la primera transcripción en la GPU tardaba 11 s (medido el 21/10/2026, la
primera respuesta del primer cliente después de cada reinicio) y las siguientes 0,4 s."""
from __future__ import annotations

import sys
import types

from app import stt


def test_al_cargar_whisper_se_hace_una_transcripcion_de_calentamiento(monkeypatch):
    llamadas = []

    class Modelo:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kw):
            llamadas.append((len(audio), kw))

            def segmentos():
                llamadas.append("consumido")  # el decodificador solo corre si se recorren los segmentos
                yield from ()

            return segmentos(), None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Modelo))
    monkeypatch.setattr(stt, "_model", None)
    stt.load_model()
    assert len(llamadas) == 2 and llamadas[1] == "consumido"
    largo, kw = llamadas[0]
    assert largo > 0 and kw.get("vad_filter") is False  # sin filtro: si no, descarta el audio y no calienta nada


def test_si_el_calentamiento_falla_el_servicio_arranca_igual(monkeypatch):
    class Modelo:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, *a, **k):
            raise RuntimeError("CUDA rara")

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Modelo))
    monkeypatch.setattr(stt, "_model", None)
    stt.load_model()
    assert stt._model is not None
