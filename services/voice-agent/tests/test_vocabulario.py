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
    # Un teléfono en dígitos solo usa palabras de la lista de números: no es eco (24/10/2026 se lo tomó por eco).
    assert not es_eco_del_prompt("Cero, nueve, ocho, uno, cuatro, cinco, seis, siete, ocho, nueve.", p)
    assert not es_eco_del_prompt("Mi número es cero nueve ocho uno cuatro cinco seis siete ocho nueve", p)
    assert es_eco_del_prompt("Uno, un, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez.", p)
    assert not es_eco_del_prompt("Quiero sacar un turno para el service de mi Renault en Zeballos.", p)


def _modelo_falso(monkeypatch, texto):
    vistos = []

    class Modelo:
        def transcribe(self, audio, **kw):
            vistos.append({"muestras": len(audio), "prompt": kw.get("initial_prompt")})
            return [types.SimpleNamespace(text=texto, avg_logprob=-0.2)], None

    monkeypatch.setattr(stt, "_model", Modelo())
    return vistos


def test_una_palabra_suelta_se_transcribe_con_margen_y_sin_la_guia_de_la_encuesta(monkeypatch):
    import asyncio

    vistos = _modelo_falso(monkeypatch, "Siete.")
    r = asyncio.run(stt.transcribe(b"\x01\x00" * 2900, ["Renault"]))  # 0,36 s: antes se descartaba por < 0,5 s
    assert r.text == "Siete." and len(vistos) == 1
    assert vistos[0]["muestras"] == 2900 * 2 + 2 * int(0.3 * 16000)  # la palabra (a 16 kHz) y 0,3 s de aire a cada lado
    assert vistos[0]["prompt"] == "Palabras que pueden aparecer: Renault."  # sin «Encuesta…» ni los números
    # Una frase de 1 a 2,5 s: sin guía de los números y sin margen (con margen se entendía peor).
    vistos.clear()
    asyncio.run(stt.transcribe(b"\x01\x00" * 11200, None))  # 1,4 s
    assert vistos[0]["prompt"] is None and vistos[0]["muestras"] == 22400
    # Una respuesta larga sí lleva la guía y no lleva margen.
    vistos.clear()
    asyncio.run(stt.transcribe(b"\x01\x00" * 32000, ["Renault"]))  # 4 s
    assert vistos[0]["prompt"].startswith(stt.PROMPT_BASE) and vistos[0]["muestras"] == 64000


def test_un_golpe_no_se_transcribe_y_las_frases_inventadas_se_descartan(monkeypatch):
    import asyncio

    vistos = _modelo_falso(monkeypatch, "¡Gracias por ver el vídeo!")
    assert asyncio.run(stt.transcribe(b"\x01\x00" * 800, None)).text == "" and vistos == []  # 0,1 s: ni se intenta
    assert asyncio.run(stt.transcribe(b"\x01\x00" * 8000, None)).text == ""  # lo que inventó Whisper con ruido
    assert stt.es_alucinacion("Suscríbete al canal") and not stt.es_alucinacion("Gracias, nada más")
