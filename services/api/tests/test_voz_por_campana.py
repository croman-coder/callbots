"""La voz se elige por campaña desde el panel."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.db import get_db

BASIC = {"authorization": "Basic " + base64.b64encode(f"{settings.admin_user}:{settings.admin_password}".encode()).decode()}


class FalsaSesion:
    def __init__(self, campana):
        self.campana, self.confirmado = campana, False

    def get(self, _modelo, _id):
        return self.campana

    def commit(self):
        self.confirmado = True


@pytest.fixture()
def hacer():
    campana = SimpleNamespace(id=1, name="PRUEBA", voice_speed=1.0, voice_pitch=1.0, voice_expressiveness=0.667,
                              voice_volume=1.0, voice_profile_id=None)
    sesion = FalsaSesion(campana)
    main.app.dependency_overrides[get_db] = lambda: sesion
    cli = TestClient(main.app, base_url="https://callbot.test", follow_redirects=False)

    def guardar(perfil):
        return cli.post("/voices/campaign/1", data={"voice_profile_id": perfil}, headers=BASIC)

    yield campana, sesion, guardar
    main.app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("perfil,esperado", [("abc123-DEF_9", "abc123-DEF_9"), ("piper", "piper"), ("", None), ("  ", None)])
def test_se_guarda_la_voz_elegida(hacer, perfil, esperado):
    campana, sesion, guardar = hacer
    r = guardar(perfil)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert campana.voice_profile_id == esperado and sesion.confirmado


@pytest.mark.parametrize("malo", ["../../etc/passwd", "a b", "x" * 65, "id;drop", "ñandú", "<script>"])
def test_una_voz_invalida_se_rechaza_sin_guardar(hacer, malo):
    campana, sesion, guardar = hacer
    r = guardar(malo)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert campana.voice_profile_id is None and not sesion.confirmado


# --- el precalentado y el voice-agent tienen que decir el saludo igual ---
CASOS_NOMBRE = [
    ("¡Hola, {nombre}! Le habla el asistente.", "Juan", "¡Hola, Juan! Le habla el asistente."),
    ("¡Hola, {nombre}! Le habla el asistente.", None, "¡Hola! Le habla el asistente."),
    ("Hola {nombre}, le hablamos del taller.", None, "Hola, le hablamos del taller."),
    ("Hola {nombre}, le hablamos del taller.", "Ana", "Hola Ana, le hablamos del taller."),
    ("Sin placeholder.", "Ana", "Sin placeholder."),
]


@pytest.mark.parametrize("plantilla,nombre,esperado", CASOS_NOMBRE)
def test_con_nombre_coincide_con_lo_que_dice_el_voice_agent(plantilla, nombre, esperado):
    from app.routers.internal import con_nombre

    assert con_nombre(plantilla, nombre) == esperado
