"""Voz de Voicebox por campaña: nada espera en llamada y cada voz tiene su audio."""
from __future__ import annotations

import asyncio
import os

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import config  # noqa: E402

PCM_VOICEBOX = b"\x01\x00" * 800
PCM_PIPER = b"\x02\x00" * 800


@pytest.fixture(autouse=True)
def _aislar(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    monkeypatch.setattr(tts, "_en_cola", {})
    monkeypatch.setattr(tts, "_tarea", None)
    monkeypatch.setattr(tts, "_engines", {"perfil-a": "kokoro", "perfil-b": "qwen"})
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    llamadas = []

    async def voicebox(text, timeout, perfil):
        llamadas.append((text, timeout, perfil))
        await asyncio.sleep(0)
        return PCM_VOICEBOX

    monkeypatch.setattr(tts, "_voicebox_synthesize", voicebox)
    monkeypatch.setattr(tts, "_piper_synthesize", lambda text, params=None: PCM_PIPER)
    return llamadas


P = tts.VoiceParams


def test_la_voz_cambia_la_clave_en_memoria_y_en_disco():
    assert P(profile_id="a").clave() != P(profile_id="b").clave() != P().clave()
    assert tts._cache_key("hola", P(profile_id="a")) != tts._cache_key("hola", P(profile_id="b"))


def test_en_llamada_una_frase_sin_cachear_habla_piper_al_instante_aunque_voicebox_tarde(monkeypatch):
    async def caso():
        liberar = asyncio.Event()

        async def voicebox_lento(text, timeout, perfil):
            await liberar.wait()  # Voicebox "tarda minutos"
            return PCM_VOICEBOX

        monkeypatch.setattr(tts, "_voicebox_synthesize", voicebox_lento)
        # Si en llamada se esperara a Voicebox, esto no volvería y wait_for lo haría fallar.
        pcm = await asyncio.wait_for(tts.synthesize("Buenos días", params=P(profile_id="perfil-a")), timeout=2)
        assert pcm == PCM_PIPER
        assert len(tts._en_cola) == 1  # quedó pedida para la próxima
        assert ("Buenos días", P(profile_id="perfil-a").clave()) not in tts._memory_cache  # y el respaldo no tapa al bueno
        liberar.set()
        await tts._tarea
        assert await tts.synthesize("Buenos días", params=P(profile_id="perfil-a")) == PCM_VOICEBOX

    asyncio.run(caso())


def test_el_segundo_plano_genera_con_la_voz_de_voicebox_y_la_proxima_llamada_la_usa(_aislar):
    async def caso():
        p = P(profile_id="perfil-a")
        await tts.synthesize("Buenos días", params=p)
        await tts._tarea
        assert _aislar and _aislar[0][2] == "perfil-a"
        assert await tts.synthesize("Buenos días", params=p) == PCM_VOICEBOX

    asyncio.run(caso())


def test_dos_campanas_con_distinta_voz_no_se_pisan(_aislar):
    async def caso():
        a, b = P(profile_id="perfil-a"), P(profile_id="perfil-b")
        await tts.warm_up(["Hola"], a)
        assert await tts.synthesize("Hola", params=a) == PCM_VOICEBOX
        # la otra voz no tiene su audio todavía: no hereda el de la primera
        assert await tts.synthesize("Hola", params=b) == PCM_PIPER

    asyncio.run(caso())


def test_piper_a_proposito_no_toca_voicebox(_aislar):
    async def caso():
        assert await tts.synthesize("Hola", params=P(profile_id="piper")) == PCM_PIPER
        assert _aislar == [] and not tts._en_cola

    asyncio.run(caso())


def test_la_cola_no_repite_ni_crece_sin_limite(_aislar):
    async def caso():
        p = P(profile_id="perfil-a")
        for _ in range(5):
            tts._encolar("misma", p)
        assert len(tts._en_cola) == 1
        for i in range(200):
            tts._encolar(f"frase {i}", p)
        assert len(tts._en_cola) <= tts._MAX_EN_COLA
        tts._tarea.cancel()

    asyncio.run(caso())
