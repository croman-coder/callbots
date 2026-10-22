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


def test_si_whisper_devuelve_el_prompt_es_eco():
    from app.stt import PROMPT_BASE, es_eco_del_prompt, prompt_para

    p = prompt_para(["Renault", "Taller Zeballos"])
    assert es_eco_del_prompt("Encuesta telefónica de satisfacción de un taller mecánico.", p)
    assert es_eco_del_prompt("Palabras que pueden aparecer: Renault, Taller Zeballos.", p)
    assert not es_eco_del_prompt("Ocho.", p) and not es_eco_del_prompt("Cero, nunca.", PROMPT_BASE)
    assert not es_eco_del_prompt("Quiero sacar un turno para el service de mi Renault en Zeballos.", p)
