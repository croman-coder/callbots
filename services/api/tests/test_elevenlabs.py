"""ElevenLabs en el panel: las voces de la cuenta, de pago, con muestra recordada y sin exponer la clave."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod, main, models, sso, voces
from app.config import settings
from app.models import Campaign
from app.services import elevenlabs, voicebox, voicestudio

H = {"Sec-Fetch-Site": "same-origin"}
RACHEL = "21m00Tcm4TlvDq8ikWAM"
CUENTA = [
    {"voice_id": RACHEL, "name": "Rachel - Calm & Clear", "category": "premade",
     "labels": {"gender": "female", "accent": "american", "use_case": "narration"}},
    {"voice_id": "Xb7hH8MSUJpSbSDYk0k2", "name": "Mi voz", "category": "cloned", "labels": {"gender": "male"}},
    {"name": "sin id"},  # una fila rota de la cuenta no tumba la lista
]


@pytest.fixture(autouse=True)
def _limpio(monkeypatch):
    elevenlabs.olvidar()
    elevenlabs._muestras.clear()
    monkeypatch.setattr(voicebox, "is_configured", lambda: False)
    monkeypatch.setattr(voicestudio, "list_profiles", lambda: [])
    monkeypatch.setattr(settings, "voicestudio_url", "")
    yield
    elevenlabs.olvidar()


@pytest.fixture()
def conectado(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-clave-de-prueba")
    monkeypatch.setattr(elevenlabs, "listar_voces", lambda: CUENTA)
    monkeypatch.setattr(elevenlabs, "consumo", lambda: {"usados": 12345, "limite": 100000, "plan": "creator"})


def test_sin_clave_no_se_ofrecen_voces_de_elevenlabs(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    assert voces.de_elevenlabs() == [] and not elevenlabs.is_configured()


def test_las_voces_de_la_cuenta_se_ofrecen_como_de_pago(conectado):
    lista = voces.de_elevenlabs()
    assert [v["id"] for v in lista] == [f"el:{RACHEL}", "el:Xb7hH8MSUJpSbSDYk0k2"]
    rachel, mia = lista
    assert rachel["nombre"] == "Rachel" and rachel["genero"] == "Mujer" and rachel["tipo"] == "Prediseñada"
    assert mia["tipo"] == "Clonada" and mia["genero"] == "Hombre"
    assert all(v["costo"] == "De pago" and v["en_vivo"] and v["escuchar"] for v in lista)
    assert "american" in rachel["descripcion"]
    # Las gratis siguen diciendo Gratis: nadie elige una voz de pago sin enterarse.
    gratis, _ = voces.catalogo()
    assert {v["costo"] for v in gratis if v["motor"] != "elevenlabs"} == {"Gratis"}


def test_el_catalogo_las_incluye_y_la_voz_elegida_se_encuentra(conectado):
    lista, _ = voces.catalogo()
    assert f"el:{RACHEL}" in [v["id"] for v in lista]
    assert voces.elegida(lista, f"el:{RACHEL}")["nombre"] == "Rachel"


def test_si_elevenlabs_no_responde_el_catalogo_sigue(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-x")

    def roto():
        raise elevenlabs.ElevenLabsError("sin red")

    monkeypatch.setattr(elevenlabs, "listar_voces", roto)
    lista, _ = voces.catalogo()
    assert [v["id"] for v in lista] == ["piper"]


def test_solo_ids_con_forma_de_voz(conectado):
    assert elevenlabs.id_elevenlabs(f"el:{RACHEL}") == RACHEL
    assert elevenlabs.id_elevenlabs("el:../../etc") is None
    assert elevenlabs.id_elevenlabs("vs:abc") is None and elevenlabs.id_elevenlabs(None) is None
    with pytest.raises(elevenlabs.ElevenLabsError):
        elevenlabs.muestra("../../x", "hola")


class _Resp:
    def __init__(self, contenido=b"ID3audio", codigo=200):
        self.content, self.status_code, self.text = contenido, codigo, ""

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("x", request=httpx.Request("POST", "http://x"), response=self)

    def json(self):
        return {}


def test_la_muestra_se_recuerda_para_no_cobrar_dos_veces(conectado, monkeypatch):
    llamadas = []
    monkeypatch.setattr(elevenlabs.httpx, "post", lambda *a, **kw: llamadas.append((a, kw)) or _Resp())
    assert elevenlabs.muestra(RACHEL, "Hola, buenas tardes.") == b"ID3audio"
    assert elevenlabs.muestra(RACHEL, "Hola, buenas tardes.") == b"ID3audio"
    assert len(llamadas) == 1
    assert llamadas[0][1]["headers"]["xi-api-key"] == "xi-clave-de-prueba"
    assert llamadas[0][1]["params"]["output_format"].startswith("mp3")
    elevenlabs.muestra(RACHEL, "Otro texto distinto.")
    assert len(llamadas) == 2


def test_un_error_de_la_cuenta_se_explica_sin_la_respuesta_cruda(conectado, monkeypatch):
    monkeypatch.setattr(elevenlabs.httpx, "post", lambda *a, **kw: _Resp(codigo=401))
    with pytest.raises(elevenlabs.ElevenLabsError) as e:
        elevenlabs.muestra(RACHEL, "Hola")
    assert "clave" in str(e.value) and "xi-" not in str(e.value)
    monkeypatch.setattr(elevenlabs.httpx, "post", lambda *a, **kw: _Resp(codigo=429))
    with pytest.raises(elevenlabs.ElevenLabsError) as e:
        elevenlabs.muestra(RACHEL, "Hola otra vez")
    assert "esperar" in str(e.value)


@pytest.fixture()
def cliente(monkeypatch):
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
    s.add(Campaign(name="Posventa", description="", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48, is_active=False))
    s.commit()
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", "admin", "crm"), settings.sso_secret))
    c.S = S
    yield c
    main.app.dependency_overrides.pop(dbmod.get_db, None)


def test_la_pantalla_de_voces_muestra_las_de_pago_y_el_consumo(conectado, cliente):
    html = cliente.get("/voices").text
    assert "Rachel" in html and "De pago" in html and "Conectado" in html
    assert "12.345" in html and "100.000" in html  # consumo con el formato de Paraguay
    assert "Una sola vez" not in html and "xi-clave" not in html  # nunca se muestra la clave


def test_sin_la_cuenta_conectada_se_explica_como_conectarla(monkeypatch, cliente):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    html = cliente.get("/voices").text
    assert "Falta conectar la cuenta de ElevenLabs" in html and "docs/elevenlabs.md" in html


def test_la_muestra_de_una_voz_de_elevenlabs_es_un_mp3(conectado, cliente, monkeypatch):
    monkeypatch.setattr(elevenlabs.httpx, "post", lambda *a, **kw: _Resp(b"ID3mp3"))
    r = cliente.get("/voices/preview", params={"profile_id": f"el:{RACHEL}"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"


def test_una_campania_puede_elegir_una_voz_de_elevenlabs(conectado, cliente):
    r = cliente.post("/campaigns/1/guardar", data={"tab": "configuracion", "voice_profile_id": f"el:{RACHEL}"}, headers=H)
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    assert cliente.S().get(Campaign, 1).voice_profile_id == f"el:{RACHEL}"
    html = cliente.get("/campaigns/1?tab=configuracion").text
    assert "Rachel" in html and "De pago" in html


def test_el_diagnostico_cuenta_las_voces(conectado, cliente):
    html = cliente.get("/health-detail").text
    assert "ElevenLabs" in html and "2 voces en la cuenta" in html
