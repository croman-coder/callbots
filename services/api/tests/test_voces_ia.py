"""Voces creadas con IA: el motor inventa la voz, no es de nadie y solo se guarda un audio que él generó."""
from __future__ import annotations

import io
import wave

import httpx
import pytest

from app import agentes, voces
from app.services import voicestudio
from tests.test_voces_clonadas import H, Sesion, cliente  # noqa: F401  (fixtures)


def _wav(segundos: float, rate: int = 24000) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * segundos))
    return b.getvalue()


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    voces._MUESTRAS.clear()
    agentes._pedidos.clear()
    yield
    voces._MUESTRAS.clear()
    agentes._pedidos.clear()


# ─────────────────────────────────────────────── rasgos
def test_los_rasgos_son_listas_cerradas():
    assert voces.instruccion_diseno("Mujer", "Joven", "Medio") == "female, young adult, moderate pitch"
    assert voces.instruccion_diseno("Hombre", "Adulta", "Grave") == "male, middle-aged, low pitch"
    for malo in [("Otro", "Joven", "Medio"), ("Mujer", "female", "Medio"), ("Mujer", "Joven", "high pitch, whisper"), ("", "", "")]:
        assert voces.instruccion_diseno(*malo) is None


# ─────────────────────────────────────────────── catálogo
def test_la_voz_ia_se_ofrece_sin_permiso_y_la_clonada_sin_permiso_no(monkeypatch):
    perfiles = [
        {"id": "ia1", "name": "Marina (mujer, IA)", "kind": "clone", "personality": voicestudio.MARCA_IA, "verified_own_voice": 0},
        {"id": "sinok", "name": "Nicole", "kind": "clone", "verified_own_voice": 0},
        {"id": "ok", "name": "Lucía (mujer)", "kind": "clone", "verified_own_voice": 1, "consent_text": "Yo, Lucía…"},
    ]
    monkeypatch.setattr(voicestudio, "list_profiles", lambda: perfiles)
    monkeypatch.setattr(voces.settings, "voicestudio_url", "http://voicestudio:3900")
    lista = {v["id"]: v for v in voces.clonadas()}
    assert set(lista) == {"vs:ia1", "vs:ok"}
    assert lista["vs:ia1"]["tipo"] == "Creada con IA" and lista["vs:ia1"]["consentimiento"] == ""
    assert lista["vs:ia1"]["en_vivo"] and lista["vs:ia1"]["genero"] == "Mujer"
    assert lista["vs:ok"]["tipo"] == "Clonada"


# ─────────────────────────────────────────────── muestras temporales
def test_las_muestras_vencen_y_son_pocas():
    c = voces.guardar_muestra(b"a", ahora=0)
    assert voces.muestra(c, ahora=60) == b"a"
    assert voces.muestra(c, ahora=voces._MUESTRAS_TTL + 1) is None  # venció
    assert voces.muestra("no-existe") is None
    for i in range(voces._MUESTRAS_MAX + 5):
        voces.guardar_muestra(b"x", ahora=100 + i)
    assert len(voces._MUESTRAS) == voces._MUESTRAS_MAX


# ─────────────────────────────────────────────── rutas
def test_probar_genera_y_la_muestra_se_escucha(cliente, monkeypatch):
    pedidos = []
    monkeypatch.setattr(voicestudio, "disenar", lambda instruccion, semilla, texto: pedidos.append((instruccion, texto)) or _wav(5))
    r = cliente.post("/voices/disenar/probar", data={"genero": "Mujer", "edad": "Joven", "tono": "Medio"}, headers=H)
    assert r.status_code == 200
    j = r.json()
    assert pedidos == [("female, young adult, moderate pitch", voces.TEXTO_DISENO)]
    a = cliente.get(j["audio"])
    assert a.status_code == 200 and a.headers["content-type"] == "audio/wav" and a.headers["cache-control"] == "no-store"
    assert cliente.get("/voices/disenar/muestra/inventado").status_code == 404


def test_cada_prueba_es_otra_variante(cliente, monkeypatch):
    semillas = []
    monkeypatch.setattr(voicestudio, "disenar", lambda i, semilla, t: semillas.append(semilla) or _wav(5))
    for _ in range(4):
        cliente.post("/voices/disenar/probar", data={"genero": "Hombre", "edad": "Adulta", "tono": "Grave"}, headers=H)
    assert len(set(semillas)) > 1


@pytest.mark.parametrize("datos", [
    {"genero": "Otro", "edad": "Joven", "tono": "Medio"},
    {"genero": "Mujer", "edad": "Joven", "tono": "high pitch, whisper"},
    {},
])
def test_probar_rechaza_rasgos_que_no_son_de_la_lista(cliente, monkeypatch, datos):
    monkeypatch.setattr(voicestudio, "disenar", lambda *a: pytest.fail("no debía llamar al motor"))
    assert cliente.post("/voices/disenar/probar", data=datos, headers=H).status_code == 400


def test_un_audio_largo_o_vacio_no_se_ofrece(cliente, monkeypatch):
    for salida in (_wav(12), b""):
        monkeypatch.setattr(voicestudio, "disenar", lambda *a, s=salida: s)
        r = cliente.post("/voices/disenar/probar", data={"genero": "Mujer", "edad": "Joven", "tono": "Medio"}, headers=H)
        assert r.status_code == 502


def test_probar_tiene_tope_por_hora(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "disenar", lambda *a: _wav(5))
    datos = {"genero": "Mujer", "edad": "Joven", "tono": "Medio"}
    codigos = [cliente.post("/voices/disenar/probar", data=datos, headers=H).status_code for _ in range(agentes.MAX_DISENO_POR_HORA + 1)]
    assert codigos[:-1] == [200] * agentes.MAX_DISENO_POR_HORA and codigos[-1] == 429


def test_guardar_fija_el_audio_que_se_escucho_no_otro(cliente, monkeypatch):
    escuchado = _wav(5)
    monkeypatch.setattr(voicestudio, "disenar", lambda *a: escuchado)
    codigo = cliente.post("/voices/disenar/probar", data={"genero": "Mujer", "edad": "Joven", "tono": "Medio"}, headers=H).json()["codigo"]
    creados = []
    monkeypatch.setattr(voicestudio, "crear_disenada", lambda nombre, audio, texto: creados.append((nombre, audio, texto)) or {"id": "nueva1"})
    r = cliente.post("/voices/disenar/guardar", data={"codigo": codigo, "nombre": "  Marina   taller ", "genero": "Mujer"}, headers=H)
    assert r.status_code == 200 and r.json() == {"id": "vs:nueva1", "nombre": "Marina taller"}
    assert creados == [("Marina taller (mujer, IA)", escuchado, voces.TEXTO_DISENO)]
    # ya se guardó: el mismo código no sirve dos veces
    again = cliente.post("/voices/disenar/guardar", data={"codigo": codigo, "nombre": "Otra", "genero": "Mujer"}, headers=H)
    assert again.status_code == 410


@pytest.mark.parametrize("datos,estado", [
    ({"codigo": "no-existe", "nombre": "Marina", "genero": "Mujer"}, 410),
    ({"codigo": "x", "nombre": "", "genero": "Mujer"}, 400),
    ({"codigo": "x", "nombre": "Marina", "genero": "Robot"}, 400),
])
def test_guardar_rechaza_lo_que_no_corresponde(cliente, monkeypatch, datos, estado):
    monkeypatch.setattr(voicestudio, "crear_disenada", lambda *a: pytest.fail("no debía crear nada"))
    assert cliente.post("/voices/disenar/guardar", data=datos, headers=H).status_code == estado


def test_la_pagina_ofrece_crear_y_borrar_una_voz_ia(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "list_profiles", lambda: [
        {"id": "ia1", "name": "Marina (mujer, IA)", "kind": "clone", "personality": voicestudio.MARCA_IA}])
    r = cliente.get("/voices")
    assert r.status_code == 200
    assert 'id="form-disenar"' in r.text and "Probar una voz" in r.text
    assert "Creada con IA" in r.text and "/voices/vs:ia1/borrar" in r.text


# ─────────────────────────────────────────────── cliente del motor
def test_crear_disenada_marca_el_perfil_y_lo_prueba(monkeypatch):
    llamadas = []

    def post(url, **kw):
        llamadas.append((url, kw))
        return httpx.Response(200, json={"id": "p1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(voicestudio.httpx, "post", post)
    monkeypatch.setattr(voicestudio.settings, "voicestudio_url", "http://voicestudio:3900")
    perfil = voicestudio.crear_disenada("Marina (mujer, IA)", _wav(5), "Hola")
    assert perfil["id"] == "p1"
    primero = llamadas[0]
    assert primero[0].endswith("/profiles") and primero[1]["data"]["personality"] == voicestudio.MARCA_IA
    assert primero[1]["data"]["kind"] == "clone" and primero[1]["files"]["ref_audio"][1].startswith(b"RIFF")
    assert llamadas[1][0].endswith("/v1/audio/speech") and llamadas[1][1]["json"]["voice"] == "p1"  # la probó


def test_si_la_voz_no_se_puede_probar_se_borra(monkeypatch):
    borrados = []

    def post(url, **kw):
        if url.endswith("/profiles"):
            return httpx.Response(200, json={"id": "p9"}, request=httpx.Request("POST", url))
        return httpx.Response(500, json={"detail": "CUDA out of memory"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(voicestudio.httpx, "post", post)
    monkeypatch.setattr(voicestudio, "borrar", lambda pid, silencioso=False: borrados.append(pid))
    monkeypatch.setattr(voicestudio.settings, "voicestudio_url", "http://voicestudio:3900")
    with pytest.raises(voicestudio.VoiceStudioError, match="otra variante"):
        voicestudio.crear_disenada("X", _wav(5), "Hola")
    assert borrados == ["p9"]


def test_disenar_pide_el_maximo_de_pasos_y_la_semilla(monkeypatch):
    visto = {}

    def post(url, **kw):
        visto.update(kw["json"])
        return httpx.Response(200, content=_wav(5), request=httpx.Request("POST", url))

    monkeypatch.setattr(voicestudio.httpx, "post", post)
    monkeypatch.setattr(voicestudio.settings, "voicestudio_url", "http://voicestudio:3900")
    voicestudio.disenar("female, young adult, moderate pitch", 42, "Hola")
    assert visto["instruct"] == "female, young adult, moderate pitch" and visto["seed"] == 42 and visto["num_step"] == 32
    assert visto["voice"] == "default" and visto["language"] == "Spanish"
