"""Whisper en PyTorch (WHISPER_BACKEND=torch): el motor de la DGX Spark, donde ctranslate2 no trae CUDA.

Sin torch ni GPU: se simulan `torch` y `transformers` y se comprueba lo que es nuestro (el prompt, las ventanas, el
puntaje y que el motor de siempre no cambia).
"""
from __future__ import annotations

import contextlib
import sys
import types

import numpy as np
import pytest

from app import stt
from app.config import WHISPER_SAMPLE_RATE, config


class _Tensor:
    def __init__(self, datos):
        self.datos = datos

    def to(self, *a, **k):
        return self


def _falsos(monkeypatch, texto="Diez.", puntaje=-0.2):
    visto = {"generate": [], "prompts": [], "ventanas": 0}

    class Proc:
        @classmethod
        def from_pretrained(cls, nombre):
            visto["nombre"] = nombre
            return cls()

        def get_prompt_ids(self, prompt):
            visto["prompts"].append(prompt)
            return [1, 2, 3]

        def __call__(self, ventana, sampling_rate, return_tensors):
            visto["ventanas"] += 1
            return types.SimpleNamespace(input_features=_Tensor(ventana))

        def batch_decode(self, secuencias, skip_special_tokens):
            return [secuencias]

    class Modelo:
        @classmethod
        def from_pretrained(cls, nombre, dtype, attn_implementation):
            visto["dtype"] = dtype
            return cls()

        def to(self, device):
            visto["device"] = device
            return self

        def eval(self):
            return self

        def generate(self, feats, **kw):
            visto["generate"].append(kw)
            return types.SimpleNamespace(sequences=texto, sequences_scores=[puntaje])

    torch = types.SimpleNamespace(float16="fp16", float32="fp32", tensor=_Tensor, no_grad=contextlib.nullcontext)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(WhisperProcessor=Proc, WhisperForConditionalGeneration=Modelo))
    return visto


def test_partir_ventanas_deja_una_sola_si_entra_y_parte_si_no():
    un_minuto = np.zeros(65 * WHISPER_SAMPLE_RATE, dtype=np.float32)
    assert len(stt.partir_ventanas(np.zeros(10 * WHISPER_SAMPLE_RATE, dtype=np.float32))) == 1
    partes = stt.partir_ventanas(un_minuto)
    assert [p.size // WHISPER_SAMPLE_RATE for p in partes] == [30, 30, 5]


def test_el_motor_torch_usa_hotwords_como_prompt_y_devuelve_el_puntaje(monkeypatch):
    visto = _falsos(monkeypatch)
    modelo = stt.WhisperTorch("openai/whisper-large-v3-turbo", "cuda")
    segmentos, info = modelo.transcribe(np.zeros(WHISPER_SAMPLE_RATE, dtype=np.float32), language="es", beam_size=5,
                                        vad_filter=False, hotwords=stt.NUMEROS)
    seg = list(segmentos)
    assert info is None and [(s.text, s.avg_logprob) for s in seg] == [("Diez.", -0.2)]
    assert visto["prompts"] == [stt.NUMEROS] and visto["generate"][0]["language"] == "es" and visto["generate"][0]["num_beams"] == 5
    assert visto["device"] == "cuda" and visto["dtype"] == "fp16"


def test_el_motor_torch_saca_del_texto_el_prompt_que_Whisper_repite(monkeypatch):
    _falsos(monkeypatch, texto=f"{stt.NUMEROS} Siete.")
    modelo = stt.WhisperTorch("x", "cuda")
    segmentos, _ = modelo.transcribe(np.zeros(WHISPER_SAMPLE_RATE, dtype=np.float32), hotwords=stt.NUMEROS)
    assert [s.text for s in segmentos] == ["Siete."]


def test_una_respuesta_larga_se_transcribe_por_ventanas_y_sin_prompt_no_se_pide_prompt(monkeypatch):
    visto = _falsos(monkeypatch, texto="hola")
    modelo = stt.WhisperTorch("x", "cpu")
    segmentos, _ = modelo.transcribe(np.zeros(65 * WHISPER_SAMPLE_RATE, dtype=np.float32))
    assert [s.text for s in segmentos] == ["hola"] * 3 and visto["ventanas"] == 3
    assert visto["prompts"] == [] and all("prompt_ids" not in kw for kw in visto["generate"]) and visto["dtype"] == "fp32"


def test_con_el_motor_torch_load_model_no_usa_faster_whisper_y_calienta(monkeypatch):
    _falsos(monkeypatch)
    creados, calientes = [], []
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=lambda *a, **k: pytest.fail("no va")))
    monkeypatch.setattr(stt, "_model", None)
    monkeypatch.setattr(stt, "_cpu_model", None)
    monkeypatch.setattr(stt, "_calentar", lambda: calientes.append(1))
    monkeypatch.setattr(stt, "_preparar_respaldo_cpu", lambda: None)
    monkeypatch.setattr(stt, "WhisperTorch", lambda nombre, device: creados.append((nombre, device)) or object())
    antes = (config.whisper_backend, config.whisper_model, config.whisper_device)
    object.__setattr__(config, "whisper_backend", "torch")
    object.__setattr__(config, "whisper_model", "openai/whisper-large-v3-turbo")
    object.__setattr__(config, "whisper_device", "cuda")
    try:
        stt.load_model()
    finally:
        for k, v in zip(("whisper_backend", "whisper_model", "whisper_device"), antes):
            object.__setattr__(config, k, v)
    assert creados == [("openai/whisper-large-v3-turbo", "cuda")] and calientes == [1]


def test_el_motor_por_defecto_sigue_siendo_ctranslate2():
    assert config.whisper_backend == "ctranslate2"
