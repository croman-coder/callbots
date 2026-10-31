"""Voces clonadas de VoiceStudio: hablan en vivo y, si el motor no está, habla la voz del servidor."""
from __future__ import annotations

import asyncio
import io
import json
import os
import time
import wave

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import config  # noqa: E402
from app.guion import duracion_estimada  # noqa: E402

PCM_ESTUDIO = b"\x03\x00" * 800
PCM_VOICEBOX = b"\x01\x00" * 800
PCM_PIPER = b"\x02\x00" * 800
P = tts.VoiceParams
_SINTETIZAR_REAL = tts._voicestudio_synthesize  # antes de que el fixture lo reemplace


@pytest.fixture(autouse=True)
def _aislar(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    monkeypatch.setattr(tts, "_en_cola", {})
    monkeypatch.setattr(tts, "_tarea", None)
    monkeypatch.setattr(tts, "_engines", {"dora": "kokoro"})
    antes = {k: getattr(config, k) for k in ("tts_cache_dir", "voicestudio_url", "voicebox_profile_id", "voicestudio_vivo_timeout", "voicestudio_api_key")}
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    object.__setattr__(config, "voicestudio_url", "http://voicestudio:3900")
    object.__setattr__(config, "voicebox_profile_id", "dora")
    llamadas = {"estudio": [], "voicebox": []}

    async def estudio(text, timeout, perfil, speed=1.0):
        llamadas["estudio"].append((text, perfil, speed))
        return PCM_ESTUDIO

    async def voicebox(text, timeout, perfil):
        llamadas["voicebox"].append((text, perfil))
        return PCM_VOICEBOX

    monkeypatch.setattr(tts, "_voicestudio_synthesize", estudio)
    monkeypatch.setattr(tts, "_voicebox_synthesize", voicebox)
    monkeypatch.setattr(tts, "_piper_synthesize", lambda text, params=None: PCM_PIPER)
    monkeypatch.setattr(tts, "nivelar", lambda pcm, volumen=1.0: pcm)
    yield llamadas
    for k, v in antes.items():
        object.__setattr__(config, k, v)


def test_un_perfil_vs_es_de_voicestudio_y_no_de_voicebox():
    assert tts.perfil_de_voicestudio(P(profile_id="vs:c672")) == "c672"
    assert tts.perfil_de_voicebox(P(profile_id="vs:c672")) is None
    assert tts.perfil_de_voicestudio(P(profile_id="dora")) is None
    object.__setattr__(config, "voicestudio_url", "")
    assert tts.perfil_de_voicestudio(P(profile_id="vs:c672")) is None
    # Sin VoiceStudio instalado, una campaña con voz clonada habla con la del servidor, no con Piper.
    assert tts.perfil_de_voicebox(P(profile_id="vs:c672")) == "dora"


def test_en_llamada_habla_la_voz_clonada_y_queda_cacheada(_aislar):
    params = P(profile_id="vs:c672", speed=1.1)
    assert asyncio.run(tts.synthesize("Buenos días", params=params)) == PCM_ESTUDIO
    assert _aislar["estudio"] == [("Buenos días", "c672", 1.1)]
    assert tts._read_cache("Buenos días", params) == PCM_ESTUDIO
    # La misma frase con la voz del servidor es otro audio.
    assert tts._cache_key("Buenos días", params) != tts._cache_key("Buenos días", P())


def test_si_voicestudio_no_contesta_habla_la_voz_del_servidor_sin_pisar_la_clonada(monkeypatch, _aislar):
    object.__setattr__(config, "voicestudio_vivo_timeout", 0.2)

    async def caido(text, timeout, perfil, speed=1.0):
        raise httpx.ConnectError("sin GPU")

    monkeypatch.setattr(tts, "_voicestudio_synthesize", caido)
    params = P(profile_id="vs:c672")
    assert asyncio.run(tts.synthesize("Hola", params=params)) == PCM_VOICEBOX
    assert _aislar["voicebox"] == [("Hola", "dora")]
    assert tts._read_cache("Hola", params) is None  # la próxima vez se vuelve a probar la clonada


def test_el_guion_de_una_voz_clonada_se_precalienta(monkeypatch):
    encoladas = []
    monkeypatch.setattr(tts, "_encolar", lambda text, params: encoladas.append(text))
    tts.precalentar_en_segundo_plano(["Buenas tardes, le habla Santa Rosa. ¿Tiene un minuto para una encuesta?"], P(profile_id="vs:c672"))
    assert encoladas == ["Buenas tardes, le habla Santa Rosa.", "¿Tiene un minuto para una encuesta?"]


def _wav(rate=24000, segundos=0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x10\x00" * int(rate * segundos))
    return buf.getvalue()


def test_pide_el_audio_como_openai_y_lo_pasa_a_8_khz(monkeypatch):
    pedidos = []

    object.__setattr__(config, "voicestudio_api_key", "clave-larga")

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer clave-larga"
        pedidos.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, content=_wav())

    cliente = httpx.AsyncClient
    monkeypatch.setattr(
        tts.httpx, "AsyncClient", lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw)
    )
    monkeypatch.setattr(tts, "_voces_ia", {"c672": False, "ia01": True})
    monkeypatch.setattr(tts, "_voces_ia_leidas", time.monotonic())
    pcm = asyncio.run(_SINTETIZAR_REAL("Hola", 2.0, "c672", speed=3))
    url, cuerpo = pedidos[0]
    assert url == "http://voicestudio:3900/v1/audio/speech"
    assert cuerpo["voice"] == "c672" and cuerpo["input"] == "Hola" and cuerpo["response_format"] == "wav"
    # Voz de una persona: se le pide la duración, a ritmo normal; sin ella OmniVoice rellena con pausas al azar.
    assert cuerpo["duration"] == duracion_estimada("Hola") and cuerpo["speed"] == 1.0
    assert len(pcm) == 2 * 800  # 0,1 s a 8 kHz: muy corto para estirarlo
    # Voz IA de Callbot: habla a su ritmo; pedirle la duración la frenaba (31/10/2026).
    asyncio.run(_SINTETIZAR_REAL("Hola", 2.0, "ia01"))
    assert "duration" not in pedidos[1][1] and pedidos[1][1]["speed"] == 1.0


def test_sin_saber_si_es_ia_no_se_pide_duracion(monkeypatch):
    pedidos = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/profiles":
            return httpx.Response(503)
        pedidos.append(json.loads(request.content))
        return httpx.Response(200, content=_wav())

    cliente = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw))
    monkeypatch.setattr(tts, "_voces_ia", {})
    monkeypatch.setattr(tts, "_voces_ia_leidas", None)
    asyncio.run(_SINTETIZAR_REAL("Buenas tardes", 2.0, "nueva"))
    assert "duration" not in pedidos[0]


def test_lee_de_voicestudio_cuales_son_voces_ia(monkeypatch):
    perfiles = [{"id": "e338eb40", "personality": "callbot-ia"}, {"id": "b7173ef4", "personality": ""}]

    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=perfiles)

    cliente = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw))
    monkeypatch.setattr(tts, "_voces_ia", {})
    monkeypatch.setattr(tts, "_voces_ia_leidas", None)
    assert asyncio.run(tts._es_voz_ia("e338eb40", {})) is True
    assert asyncio.run(tts._es_voz_ia("b7173ef4", {})) is False
    assert asyncio.run(tts._es_voz_ia("otra", {})) is None
