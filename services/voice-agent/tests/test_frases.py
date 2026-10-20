"""El bot no habla de corrido: cada frase va por separado, con una pausa entre una y otra."""
from __future__ import annotations

import asyncio
import os

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import SAMPLE_RATE, config  # noqa: E402
from app.guion import partir_frases  # noqa: E402

SALUDO = (
    "¡Hola, Marta! Le habla el asistente del taller de Santa Rosa. "
    "Queríamos saber cómo le fue en su última visita. ¿Tiene un minutito para unas preguntas cortas?"
)


def test_el_saludo_se_parte_en_frases():
    assert partir_frases(SALUDO) == [
        "¡Hola, Marta! Le habla el asistente del taller de Santa Rosa.",
        "Queríamos saber cómo le fue en su última visita.",
        "¿Tiene un minutito para unas preguntas cortas?",
    ]


def test_una_frase_corta_se_pega_a_la_siguiente():
    assert partir_frases("Perfecto. ¿Y con el plazo de entrega?") == ["Perfecto. ¿Y con el plazo de entrega?"]
    assert partir_frases("Hola.") == ["Hola."]
    assert partir_frases("  ") == []


def test_la_pregunta_final_va_despues_de_una_pausa():
    texto = "Son unas preguntas cortas, del cero al diez. ¿Tiene un minutito?"
    assert partir_frases(texto) == ["Son unas preguntas cortas, del cero al diez.", "¿Tiene un minutito?"]


def test_partir_una_frase_ya_partida_da_lo_mismo():
    # El audio se cachea por frase y warm_up vuelve a partir lo que recibe.
    for frase in partir_frases(SALUDO):
        assert partir_frases(frase) == [frase]


@pytest.fixture
def _frases(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    dichas = []

    async def una(text, timeout, params):
        dichas.append(text)
        return b"\x01\x00" * 100

    monkeypatch.setattr(tts, "_sintetizar_frase", una)
    return dichas


def test_synthesize_une_las_frases_con_una_pausa(_frases):
    pcm = asyncio.run(tts.synthesize(SALUDO))
    pausa = SAMPLE_RATE * 2 * tts.PAUSA_ENTRE_FRASES_MS // 1000
    assert _frases == partir_frases(SALUDO)
    assert pcm.count(b"\x00" * pausa) == 2
    assert len(pcm) == 3 * 200 + 2 * pausa


def test_synthesize_de_una_sola_frase_no_agrega_pausa(_frases):
    pcm = asyncio.run(tts.synthesize("Gracias por su tiempo, que tenga un buen día."))
    assert len(pcm) == 200
