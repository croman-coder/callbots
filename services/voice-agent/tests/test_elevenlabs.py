"""Voces de ElevenLabs: de pago por carácter, así que se genera una vez, hay tope diario y se cae sin ruido."""
from __future__ import annotations

import asyncio
import json
import os

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import config  # noqa: E402

VOZ = "21m00Tcm4TlvDq8ikWAM"
PCM_ELEVEN = b"\x03\x00" * 800
PCM_VOICEBOX = b"\x01\x00" * 800
PCM_PIPER = b"\x02\x00" * 800
P = tts.VoiceParams
_REAL = tts._elevenlabs_synthesize
CLAVES = ("tts_cache_dir", "elevenlabs_api_key", "elevenlabs_model", "elevenlabs_max_caracteres_dia", "voicebox_profile_id")


@pytest.fixture(autouse=True)
def _aislar(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    monkeypatch.setattr(tts, "_en_cola", {})
    monkeypatch.setattr(tts, "_tarea", None)
    monkeypatch.setattr(tts, "_engines", {"dora": "kokoro"})
    monkeypatch.setattr(tts, "_gasto_eleven", {"dia": "", "caracteres": 0, "avisado": False})
    antes = {k: getattr(config, k) for k in CLAVES}
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    object.__setattr__(config, "elevenlabs_api_key", "xi-clave-de-prueba")
    object.__setattr__(config, "elevenlabs_model", "eleven_multilingual_v2")
    object.__setattr__(config, "elevenlabs_max_caracteres_dia", 1000)
    object.__setattr__(config, "voicebox_profile_id", "dora")
    llamadas = {"eleven": [], "voicebox": []}

    async def eleven(text, timeout, voz, speed=1.0):
        llamadas["eleven"].append((text, voz, speed))
        return PCM_ELEVEN

    async def voicebox(text, timeout, perfil):
        llamadas["voicebox"].append((text, perfil))
        return PCM_VOICEBOX

    monkeypatch.setattr(tts, "_elevenlabs_synthesize", eleven)
    monkeypatch.setattr(tts, "_voicebox_synthesize", voicebox)
    monkeypatch.setattr(tts, "_piper_synthesize", lambda text, params=None: PCM_PIPER)
    monkeypatch.setattr(tts, "nivelar", lambda pcm, volumen=1.0: pcm)
    yield llamadas
    for k, v in antes.items():
        object.__setattr__(config, k, v)


def test_un_perfil_el_es_de_elevenlabs_solo_con_clave_y_con_un_id_valido():
    assert tts.perfil_de_elevenlabs(P(profile_id=f"el:{VOZ}")) == VOZ
    assert tts.perfil_de_voicebox(P(profile_id=f"el:{VOZ}")) is None
    assert tts.perfil_de_elevenlabs(P(profile_id="el:../../x")) is None  # el id va en una URL: nada raro
    assert tts.perfil_de_elevenlabs(P(profile_id="dora")) is None
    object.__setattr__(config, "elevenlabs_api_key", "")
    assert tts.perfil_de_elevenlabs(P(profile_id=f"el:{VOZ}")) is None
    # Sin clave, una campaña con voz de ElevenLabs habla con la del servidor, no con Piper.
    assert tts.perfil_de_voicebox(P(profile_id=f"el:{VOZ}")) == "dora"


def test_habla_la_voz_de_la_cuenta_y_queda_guardada(_aislar):
    params = P(profile_id=f"el:{VOZ}", speed=1.1)
    assert asyncio.run(tts.synthesize("Buenos días, Marta", params=params)) == PCM_ELEVEN
    assert _aislar["eleven"] == [("Buenos días, Marta", VOZ, 1.1)]
    # Segunda vez: sale del disco y NO se vuelve a pagar.
    tts._memory_cache.clear()
    assert asyncio.run(tts.synthesize("Buenos días, Marta", params=params)) == PCM_ELEVEN
    assert len(_aislar["eleven"]) == 1
    # Otra voz u otro modelo es otro audio.
    assert tts._cache_key("Hola", params) != tts._cache_key("Hola", P(profile_id="el:AAAAAAAAAAAA"))
    antes = tts._cache_key("Hola", params)
    object.__setattr__(config, "elevenlabs_model", "eleven_flash_v2_5")
    assert tts._cache_key("Hola", params) != antes  # otro modelo suena distinto: no puede reusar el audio


def test_si_elevenlabs_falla_habla_la_voz_del_servidor_sin_cachear(monkeypatch, _aislar):
    async def caido(text, timeout, voz, speed=1.0):
        raise httpx.ConnectError("sin internet")

    monkeypatch.setattr(tts, "_elevenlabs_synthesize", caido)
    params = P(profile_id=f"el:{VOZ}")
    assert asyncio.run(tts.synthesize("Hola", params=params)) == PCM_VOICEBOX
    assert tts._read_cache("Hola", params) is None  # la próxima vez se vuelve a probar con ElevenLabs


def test_un_error_de_cuenta_tambien_cae_sin_ruido(monkeypatch, _aislar):
    async def sin_creditos(text, timeout, voz, speed=1.0):
        raise httpx.HTTPStatusError("quota", request=httpx.Request("POST", "http://x"), response=httpx.Response(401))

    monkeypatch.setattr(tts, "_elevenlabs_synthesize", sin_creditos)
    assert asyncio.run(tts.synthesize("Hola", params=P(profile_id=f"el:{VOZ}"))) == PCM_VOICEBOX


def test_tope_diario_de_caracteres(_aislar):
    object.__setattr__(config, "elevenlabs_max_caracteres_dia", 30)
    params = P(profile_id=f"el:{VOZ}")
    assert asyncio.run(tts.synthesize("Buenos días a todos", params=params)) == PCM_ELEVEN  # 19 de 30
    # Esta ya no entra (19 + 19 > 30): sale con la voz del servidor y no se cobra.
    assert asyncio.run(tts.synthesize("Gracias por su tiempo.", params=params)) == PCM_VOICEBOX
    assert len(_aislar["eleven"]) == 1
    # Lo que ya estaba guardado sigue sonando igual, haya tope o no.
    tts._memory_cache.clear()
    assert asyncio.run(tts.synthesize("Buenos días a todos", params=params)) == PCM_ELEVEN
    assert len(_aislar["eleven"]) == 1


def test_el_tope_empieza_de_cero_cada_dia(monkeypatch, _aislar):
    object.__setattr__(config, "elevenlabs_max_caracteres_dia", 20)
    tts._gasto_eleven.update(dia="2000-01-01", caracteres=999, avisado=True)
    assert tts._hay_presupuesto_elevenlabs(10) is True  # otro día: se reinicia
    assert tts._gasto_eleven["caracteres"] == 10


def test_el_guion_de_una_voz_de_elevenlabs_se_precalienta(monkeypatch):
    encoladas = []
    monkeypatch.setattr(tts, "_encolar", lambda text, params: encoladas.append(text))
    tts.precalentar_en_segundo_plano(["Buenas tardes, le habla Santa Rosa. ¿Tiene un minuto para una encuesta?"], P(profile_id=f"el:{VOZ}"))
    assert len(encoladas) == 2


def test_pide_el_audio_en_pcm_y_lo_pasa_a_8_khz(monkeypatch):
    vistos = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append((str(request.url), request.headers["xi-api-key"], json.loads(request.content)))
        return httpx.Response(200, content=(np.ones(1600, dtype=np.int16) * 1000).tobytes())  # 0,1 s a 16 kHz

    cliente = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw))
    pcm = asyncio.run(_REAL("Hola, ¿qué tal?", 5.0, VOZ, speed=3))
    url, clave, cuerpo = vistos[0]
    assert url == f"https://api.elevenlabs.io/v1/text-to-speech/{VOZ}?output_format=pcm_16000"
    assert clave == "xi-clave-de-prueba"
    assert cuerpo["text"] == "Hola, ¿qué tal?" and cuerpo["model_id"] == "eleven_multilingual_v2"
    assert cuerpo["voice_settings"]["speed"] == 1.2  # con tope
    assert "language_code" not in cuerpo  # multilingual v2 no lo acepta
    assert len(pcm) == 2 * 800  # 0,1 s a 8 kHz


def test_los_modelos_v2_5_fijan_el_idioma(monkeypatch):
    object.__setattr__(config, "elevenlabs_model", "eleven_flash_v2_5")
    vistos = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append(json.loads(request.content))
        return httpx.Response(200, content=b"\x01\x00" * 4000)

    cliente = httpx.AsyncClient
    monkeypatch.setattr(tts.httpx, "AsyncClient", lambda **kw: cliente(transport=httpx.MockTransport(responder), **kw))
    asyncio.run(_REAL("Hola", 5.0, VOZ))
    assert vistos[0]["language_code"] == "es"


def test_un_audio_vacio_no_se_toma_por_voz(monkeypatch):
    cliente = httpx.AsyncClient
    monkeypatch.setattr(
        tts.httpx, "AsyncClient",
        lambda **kw: cliente(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b'{"detail":"x"}')), **kw),
    )
    with pytest.raises(ValueError):
        asyncio.run(_REAL("Hola", 5.0, VOZ))
