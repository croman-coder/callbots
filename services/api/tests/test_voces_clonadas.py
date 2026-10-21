"""Voces clonadas con VoiceStudio: solo con permiso grabado, se listan, se escuchan y se borran."""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod, main, models, sso, voces
from app.config import settings
from app.models import Campaign
from app.services import voicebox, voicestudio

H = {"Sec-Fetch-Site": "same-origin"}
PERFILES = [
    {"id": "c672", "name": "Lucía (mujer)", "kind": "clone", "verified_own_voice": 1, "consent_text": "Yo, Lucía…"},
    {"id": "sinok", "name": "Nicole", "kind": "clone", "verified_own_voice": 0},
    {"id": "dis", "name": "Diseñada", "kind": "design", "verified_own_voice": 1},
]


@pytest.fixture()
def Sesion(monkeypatch):
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(motor)
    S = sessionmaker(motor, expire_on_commit=False)

    def get_db():
        s = S()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[dbmod.get_db] = get_db
    monkeypatch.setattr(dbmod, "SessionLocal", S)
    s = S()
    s.add_all([
        Campaign(name="Posventa", description="", bitrix_entity_type_id=2, trigger_field="campo", delay_hours=48,
                 is_active=False, voice_profile_id="vs:c672"),
        Campaign(name="Entregas", description="", bitrix_entity_type_id=2, trigger_field="campo", delay_hours=48,
                 is_active=False),
    ])
    s.commit()
    monkeypatch.setattr(settings, "voicestudio_url", "http://voicestudio:3900")
    monkeypatch.setattr(voicebox, "is_configured", lambda: False)
    monkeypatch.setattr(voicestudio, "list_profiles", lambda: PERFILES)
    yield S
    main.app.dependency_overrides.pop(dbmod.get_db, None)


@pytest.fixture()
def cliente(Sesion):
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", "admin", "crm"), settings.sso_secret))
    return c


def test_solo_se_ofrecen_las_clonadas_con_permiso(Sesion):
    lista = voces.clonadas()
    assert [v["id"] for v in lista] == ["vs:c672"]
    assert lista[0]["nombre"] == "Lucía" and lista[0]["genero"] == "Mujer" and lista[0]["en_vivo"]


def test_la_pagina_muestra_la_biblioteca_y_quien_usa_cada_voz(cliente):
    r = cliente.get("/voices")
    assert r.status_code == 200
    assert "Lucía" in r.text and "Posventa" in r.text and 'id="form-clonar"' in r.text
    assert "Nicole" not in r.text  # sin permiso registrado no aparece


def test_sin_voicestudio_clonar_figura_como_pronto(cliente, monkeypatch):
    monkeypatch.setattr(settings, "voicestudio_url", "")
    r = cliente.get("/voices")
    assert "Pronto" in r.text and 'id="form-clonar"' not in r.text
    assert cliente.post("/voices/clonar", data={}, headers=H).status_code == 503


def _clonar(cliente, **cambios):
    datos = {"nombre": "Lucía, taller", "genero": "Mujer", "persona": "Lucía  Benítez", "acepto": "si",
             "texto": voces.TEXTO_REFERENCIA}
    datos.update(cambios)
    archivos = {"referencia": ("referencia.wav", b"RIFF" + b"\0" * 2000, "audio/wav"),
                "consentimiento": ("consentimiento.wav", b"RIFF" + b"\0" * 2000, "audio/wav")}
    return cliente.post("/voices/clonar", data=datos, files=archivos, headers={**H, "Accept": "application/json"})


def test_sin_permiso_no_se_clona(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "clonar", lambda *a: pytest.fail("no debía llamar a VoiceStudio"))
    r = _clonar(cliente, acepto="")
    assert r.status_code == 400 and "permiso" in r.json()["error"]
    assert _clonar(cliente, genero="").status_code == 400
    assert _clonar(cliente, texto="hola").status_code == 400


def test_clonar_guarda_el_permiso_con_la_persona_y_quien_la_cargo(cliente, monkeypatch):
    pedidos = []

    def clonar(nombre, ref, ref_nombre, texto, perm, perm_nombre, permiso):
        pedidos.append((nombre, texto, permiso))
        return {"id": "nuevo1", "name": nombre}

    monkeypatch.setattr(voicestudio, "clonar", clonar)
    r = _clonar(cliente)
    assert r.status_code == 200 and r.json() == {"id": "vs:nuevo1", "nombre": "Lucía, taller"}
    nombre, texto, permiso = pedidos[0]
    assert nombre == "Lucía, taller (mujer)"  # el género viaja en el nombre, como en las de Voicebox
    assert texto == voces.TEXTO_REFERENCIA
    assert "Yo, Lucía Benítez, autorizo" in permiso and "ana@santarosa.com.py" in permiso


def test_borrar_una_voz_pasa_sus_agentes_a_la_voz_del_servidor(cliente, Sesion, monkeypatch):
    borradas = []
    monkeypatch.setattr(voicestudio, "borrar", lambda pid, silencioso=False: borradas.append(pid))
    r = cliente.post("/voices/vs:c672/borrar", headers=H)
    assert r.status_code == 303 and "Voz+borrada" in r.headers["location"]
    assert borradas == ["c672"]
    assert Sesion().get(Campaign, 1).voice_profile_id is None
    # Las de Voicebox o Piper no se borran desde acá.
    assert "error=" in cliente.post("/voices/piper/borrar", headers=H).headers["location"]


def test_la_muestra_de_una_clonada_la_genera_voicestudio(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "muestra", lambda pid, texto: b"RIFF" + pid.encode())
    r = cliente.get("/voices/preview", params={"profile_id": "vs:c672"})
    assert r.status_code == 200 and r.content == b"RIFFc672"


def test_un_agente_puede_elegir_una_voz_clonada(cliente, Sesion):
    r = cliente.post("/campaigns/2/guardar", data={"tab": "configuracion", "voice_profile_id": "vs:c672"}, headers=H)
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    assert Sesion().get(Campaign, 2).voice_profile_id == "vs:c672"


def test_si_el_permiso_no_queda_se_borra_la_voz(monkeypatch):
    monkeypatch.setattr(settings, "voicestudio_url", "http://voicestudio:3900")
    monkeypatch.setattr(settings, "voicestudio_api_key", "clave-larga")
    vistos = []

    def responder(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer clave-larga"
        vistos.append((request.method, request.url.path))
        if request.url.path == "/profiles":
            return httpx.Response(200, json={"id": "p1", "name": "X"})
        if request.url.path.endswith("/consent"):
            return httpx.Response(422, json={"detail": "consent recording is too short"})
        return httpx.Response(200, json={"deleted": "p1"})

    transporte = httpx.MockTransport(responder)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Client(transport=transporte).post(*a, **kw))
    monkeypatch.setattr(httpx, "delete", lambda *a, **kw: httpx.Client(transport=transporte).delete(*a, **kw))
    with pytest.raises(voicestudio.VoiceStudioError):
        voicestudio.clonar("X", b"a", "r.wav", "texto", b"b", "c.wav", "permiso")
    assert vistos == [("POST", "/profiles"), ("POST", "/profiles/p1/consent"), ("DELETE", "/profiles/p1")]


def _wav(segundos: float, rate: int = 8000) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * segundos))
    return buf.getvalue()


def test_una_grabacion_larga_se_rechaza_antes_de_llegar_a_la_placa(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "clonar", lambda *a: pytest.fail("no debía llamar a VoiceStudio"))
    datos = {"nombre": "Lucía", "genero": "Mujer", "persona": "Lucía Benítez", "acepto": "si", "texto": voces.TEXTO_REFERENCIA}
    r = cliente.post("/voices/clonar", data=datos, headers={**H, "Accept": "application/json"},
                     files={"referencia": ("r.wav", _wav(12), "audio/wav"), "consentimiento": ("c.wav", _wav(3), "audio/wav")})
    assert r.status_code == 400 and "12 segundos" in r.json()["error"] and "unos 5" in r.json()["error"]


def test_una_grabacion_corta_pasa(cliente, monkeypatch):
    monkeypatch.setattr(voicestudio, "clonar", lambda *a: {"id": "nuevo2", "name": "x"})
    datos = {"nombre": "Lucía", "genero": "Mujer", "persona": "Lucía Benítez", "acepto": "si", "texto": voces.TEXTO_REFERENCIA}
    r = cliente.post("/voices/clonar", data=datos, headers={**H, "Accept": "application/json"},
                     files={"referencia": ("r.wav", _wav(5), "audio/wav"), "consentimiento": ("c.wav", _wav(9), "audio/wav")})
    assert r.status_code == 200


def test_la_frase_de_lectura_es_corta_y_el_tope_esta_en_la_pantalla(cliente):
    assert len(voces.TEXTO_REFERENCIA.split()) <= 14
    html = cliente.get("/voices").text
    assert 'data-max="7"' in html and "unos 5 segundos" in html


def test_si_la_voz_no_entra_en_la_placa_se_borra_y_se_explica(monkeypatch):
    monkeypatch.setattr(settings, "voicestudio_url", "http://voicestudio:3900")
    vistos = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistos.append((request.method, request.url.path))
        if request.url.path == "/profiles":
            return httpx.Response(200, json={"id": "p9", "name": "X"})
        if request.url.path.endswith("/consent"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/audio/speech":
            return httpx.Response(500, json={"detail": "CUDA out of memory"})
        return httpx.Response(200, json={"deleted": "p9"})

    transporte = httpx.MockTransport(responder)
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: httpx.Client(transport=transporte).post(*a, **kw))
    monkeypatch.setattr(httpx, "delete", lambda *a, **kw: httpx.Client(transport=transporte).delete(*a, **kw))
    with pytest.raises(voicestudio.VoiceStudioError) as e:
        voicestudio.clonar("X", b"a", "r.wav", "texto", b"b", "c.wav", "permiso")
    assert "más corta" in str(e.value) and "memory" not in str(e.value).lower()  # no se le muestra el error técnico
    assert vistos[-1] == ("DELETE", "/profiles/p9")
    assert ("POST", "/v1/audio/speech") in vistos


def test_duracion_wav():
    assert voicestudio.duracion_wav(_wav(5)) == 5.0
    assert voicestudio.duracion_wav(b"no es un wav") is None
