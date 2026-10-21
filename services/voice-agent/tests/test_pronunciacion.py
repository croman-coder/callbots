"""Diccionario de pronunciación: el bot dice «Renó», no «Renault», pero el guion y el caché siguen con lo escrito."""
from __future__ import annotations

import asyncio
import os

os.environ["VOICEBOX_URL"] = ""
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

from app import pronunciacion, tts  # noqa: E402
from app.config import config  # noqa: E402

D = [
    {"palabra": "Renault", "dicho": "Renó"},
    {"palabra": "GWM", "dicho": "Ge Doble Ve Eme"},
    {"palabra": "Santa Rosa", "dicho": "Santa Rosa Automotores"},
    {"palabra": "Santa", "dicho": "Santa María"},
]

# Los mismos casos están en services/api/tests/test_pronunciaciones.py: las dos copias tienen que coincidir.
CASOS = [
    ("Le llamo por su Renault.", "Le llamo por su Renó."),
    ("RENAULT y renault", "Renó y Renó"),
    ("Una GWM nueva", "Una Ge Doble Ve Eme nueva"),
    ("Renaulta no es una marca", "Renaulta no es una marca"),  # palabra entera
    ("Santa Rosa le saluda", "Santa Rosa Automotores le saluda"),  # la más larga gana y no se reemplaza dos veces
    ("Santa Lucía", "Santa María Lucía"),
    ("¿Es una Renault, una GWM?", "¿Es una Renó, una Ge Doble Ve Eme?"),
    ("", ""),
]


@pytest.mark.parametrize("texto,esperado", CASOS)
def test_aplicar(texto, esperado):
    assert pronunciacion.aplicar(texto, D) == esperado


def test_sin_diccionario_o_con_entradas_rotas_no_cambia_nada():
    assert pronunciacion.aplicar("Renault", []) == "Renault"
    assert pronunciacion.aplicar("Renault", [{"palabra": "", "dicho": "x"}, {"palabra": "Renault"}]) == "Renault"


def test_los_simbolos_de_una_palabra_no_son_una_expresion_regular():
    assert pronunciacion.aplicar("S.A. y SxA", [{"palabra": "S.A.", "dicho": "Sociedad Anónima"}]) == "Sociedad Anónima y SxA"


# ─────────────────────────────────────────────── en el sintetizador
@pytest.fixture(autouse=True)
def _aislar(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    monkeypatch.setattr(tts, "_en_cola", {})
    monkeypatch.setattr(tts, "_pronunciaciones", [])
    antes = config.tts_cache_dir
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    dicho = []
    monkeypatch.setattr(tts, "_piper_synthesize", lambda text, params=None: dicho.append(text) or b"\x01\x00" * 800)
    monkeypatch.setattr(tts, "nivelar", lambda pcm, volumen=1.0: pcm)
    yield dicho
    object.__setattr__(config, "tts_cache_dir", antes)


def test_el_motor_recibe_lo_que_se_dice_y_no_lo_que_se_escribe(_aislar):
    tts.set_pronunciaciones(D)
    asyncio.run(tts.synthesize("Hola, le llamo por su Renault nueva."))
    assert _aislar == ["Hola, le llamo por su Renó nueva."]


def test_el_audio_cacheado_se_reusa_y_cambiar_una_palabra_lo_regenera(_aislar):
    tts.set_pronunciaciones(D)
    frase = "Hola, le llamo por su Renault nueva."
    asyncio.run(tts.synthesize(frase))
    asyncio.run(tts.synthesize(frase))
    assert len(_aislar) == 1  # la segunda salió del caché (memoria o disco)
    tts._memory_cache.clear()
    asyncio.run(tts.synthesize(frase))
    assert len(_aislar) == 1  # y del disco, aunque se haya perdido la memoria

    # Cambia cómo se dice una palabra de esta frase: hay que generar de nuevo.
    tts.set_pronunciaciones([{"palabra": "Renault", "dicho": "Rinó"}] + D[1:])
    asyncio.run(tts.synthesize(frase))
    assert _aislar[-1] == "Hola, le llamo por su Rinó nueva." and len(_aislar) == 2


def test_cambiar_una_palabra_ajena_no_regenera_la_frase_en_disco(_aislar):
    tts.set_pronunciaciones(D)
    frase = "Hola, buenas tardes, ¿cómo está usted hoy?"
    asyncio.run(tts.synthesize(frase))
    tts.set_pronunciaciones(D + [{"palabra": "Zeekr", "dicho": "Zíker"}])  # no aparece en la frase
    asyncio.run(tts.synthesize(frase))
    assert len(_aislar) == 1  # la clave de disco lleva lo que se dice, que no cambió


def test_set_pronunciaciones_ignora_lo_que_no_es_una_entrada(_aislar):
    tts.set_pronunciaciones([None, "x", {"palabra": "A"}, {"palabra": "B", "dicho": "Be"}])
    assert tts._pronunciaciones == [{"palabra": "B", "dicho": "Be"}]
    tts.set_pronunciaciones(None)
    assert tts._pronunciaciones == []
