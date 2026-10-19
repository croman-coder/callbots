"""El panel habla con cada perfil de Voicebox con SU motor."""
from __future__ import annotations

import json

import httpx
import pytest

from app.services import voicebox


@pytest.fixture(autouse=True)
def _url(monkeypatch):
    monkeypatch.setattr(voicebox.settings, "voicebox_url", "http://voicebox:17493")
    monkeypatch.setattr(voicebox.settings, "voicebox_engine", "qwen")


@pytest.mark.parametrize("perfil,esperado", [
    ({"voice_type": "preset", "preset_engine": "kokoro", "default_engine": "kokoro"}, "kokoro"),
    ({"voice_type": "preset", "preset_engine": "qwen_custom_voice"}, "qwen_custom_voice"),
    ({"voice_type": "cloned", "default_engine": "chatterbox"}, "chatterbox"),
    ({"voice_type": "cloned"}, "qwen"),
    ({}, "qwen"),
])
def test_engine_de_cada_perfil(perfil, esperado):
    assert voicebox.engine_de(perfil) == esperado


def test_generar_con_una_voz_preset_manda_su_motor_y_no_el_global(monkeypatch):
    enviados = []

    def get(url, **kw):
        return httpx.Response(200, json={"voice_type": "preset", "preset_engine": "kokoro"}, request=httpx.Request("GET", url))

    def post(url, **kw):
        enviados.append(kw["json"])
        return httpx.Response(200, content=b"RIFFwav", request=httpx.Request("POST", url))

    monkeypatch.setattr(voicebox.httpx, "get", get)
    monkeypatch.setattr(voicebox.httpx, "post", post)
    assert voicebox.generate("perfil-1", "Hola") == b"RIFFwav"
    assert enviados[0]["engine"] == "kokoro" and enviados[0]["profile_id"] == "perfil-1"


def test_si_no_se_puede_leer_el_perfil_no_se_adivina_el_motor(monkeypatch):
    def get(url, **kw):
        raise httpx.ConnectError("voicebox caído")

    monkeypatch.setattr(voicebox.httpx, "get", get)
    with pytest.raises(voicebox.VoiceboxError, match="No se pudo leer el perfil"):
        voicebox.generate("perfil-1", "Hola")
