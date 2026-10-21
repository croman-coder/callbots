"""Las palabras del agente (marcas, modelos, sucursales) llegan al prompt de Whisper."""
from __future__ import annotations

import types

from app import dialog, stt
from tests.test_buzon_y_transferencia import Api, correr, entorno, guion, habla, silencio, verdict  # noqa: F401


def test_el_prompt_suma_las_palabras_sin_perder_el_sesgo_a_los_numeros():
    assert stt.prompt_para(None) == stt.PROMPT_BASE and stt.prompt_para(["", "  "]) == stt.PROMPT_BASE
    p = stt.prompt_para(["Renault", "Kardian", " Mariano Roque Alonso "])
    assert p.startswith(stt.PROMPT_BASE) and p.endswith("Palabras que pueden aparecer: Renault, Kardian, Mariano Roque Alonso.")


def test_cada_respuesta_se_transcribe_con_el_vocabulario_del_agente(entorno, monkeypatch):
    vistos = []

    async def transcribir(pcm, vocabulario=None):
        vistos.append(vocabulario)
        return types.SimpleNamespace(text="nueve", confidence=0.9)

    monkeypatch.setattr(stt, "transcribe", transcribir)
    entorno.escuchas += [silencio(), habla(1)]
    d, _ = correr(guion(vocabulario=["Renault", "Kardian"]), Api(verdicts=[verdict()]))
    assert vistos == [["Renault", "Kardian"]] and d.outcome == dialog.Outcome.COMPLETED
