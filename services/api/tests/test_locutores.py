"""Voz de locutor a distancia: el enlace, la página pública donde graba, lo que se acepta y la revisión en Voces."""
from __future__ import annotations

import io
import wave
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import agentes, locutores
from app.config import settings
from app.models import GrabacionVoz
from app.services import voicestudio
from tests.test_voces_clonadas import H, Sesion, cliente  # noqa: F401


def _wav(segundos: float, rate: int = 16000) -> bytes:
    salida = io.BytesIO()
    with wave.open(salida, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(segundos * rate))
    return salida.getvalue()


@pytest.fixture(autouse=True)
def carpeta(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "recordings_dir", str(tmp_path))
    monkeypatch.setattr(settings, "panel_url", "https://callbot.santarosa.lat")
    monkeypatch.setattr(agentes, "permitir", lambda clave: True)
    return tmp_path


def _enlace(cliente, persona="Pedro Benítez", genero="Hombre") -> GrabacionVoz:  # noqa: F811
    r = cliente.post("/voices/locutores", data={"persona": persona, "genero": genero, "nota": "Locutor"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"] and r.headers["location"].endswith("#locutores")
    return None


def _ultima(Sesion) -> GrabacionVoz:  # noqa: N803, F811
    from sqlalchemy import select

    return Sesion().scalars(select(GrabacionVoz).order_by(GrabacionVoz.id.desc())).first()


def _archivos(permiso: float = 8, toma: float = 4, cuantas: int = 5) -> dict:
    archivos = {"consentimiento": ("consentimiento.wav", _wav(permiso), "audio/wav")}
    for i in range(1, cuantas + 1):
        archivos[f"toma_{i}"] = (f"toma_{i}.wav", _wav(toma), "audio/wav")
    return archivos


def test_frases_con_el_articulo_del_genero():
    assert locutores.frases("Mujer")[0].startswith("Hola, buenas tardes. Le habla la asistente")
    assert "el asistente" in locutores.frases("Hombre")[0]
    assert any("Mariano Roque Alonso" in f for f in locutores.frases("Hombre"))


def test_crear_el_enlace_pide_nombre_y_genero(cliente, Sesion):  # noqa: F811
    r = cliente.post("/voices/locutores", data={"persona": " ", "genero": "Hombre"}, headers=H)
    assert "error=" in r.headers["location"]
    r = cliente.post("/voices/locutores", data={"persona": "Pedro", "genero": "Otro"}, headers=H)
    assert "error=" in r.headers["location"]
    _enlace(cliente)
    g = _ultima(Sesion)
    assert g.estado == "esperando" and g.creada_por == "ana@santarosa.com.py" and len(g.token) >= 30
    assert locutores.enlace(g) == f"https://callbot.santarosa.lat/grabar/{g.token}"
    pagina = cliente.get("/voices").text
    assert "Voz de un locutor, a distancia" in pagina and locutores.enlace(g) in pagina and "Copiar enlace" in pagina


def test_la_pagina_publica_no_pide_sesion(cliente, Sesion):  # noqa: F811
    _enlace(cliente)
    g = _ultima(Sesion)
    anonimo = TestClient(cliente.app, follow_redirects=False)
    r = anonimo.get(f"/grabar/{g.token}")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "noindex" in r.headers["x-robots-tag"]
    assert "Hola, Pedro." in r.text and "Yo, Pedro Benítez, autorizo" in r.text and "Frase 5 de 5" in r.text
    assert "Ciudad del Este" in r.text and "Enviar grabaciones" in r.text
    assert "Este enlace no existe." in anonimo.get("/grabar/cualquiera").text
    assert anonimo.post("/voices/locutores", data={"persona": "X", "genero": "Hombre"}, headers=H).status_code in (303, 401, 403)
    assert _ultima(Sesion).id == g.id  # sin sesión no se crean enlaces


def test_recibe_las_grabaciones_una_sola_vez(cliente, Sesion, carpeta):  # noqa: F811
    _enlace(cliente)
    g = _ultima(Sesion)
    anonimo = TestClient(cliente.app, follow_redirects=False)
    r = anonimo.post(f"/grabar/{g.token}", data={"acepto": "si"}, files=_archivos())
    assert r.status_code == 200 and r.json() == {"ok": True}
    g = _ultima(Sesion)
    assert g.estado == "recibida" and len(g.tomas) == 5 and g.tomas[0]["segundos"] == 4.0
    assert "Grabado a distancia" in g.consentimiento_texto and "Yo, Pedro Benítez" in g.consentimiento_texto
    guardados = sorted(p.name for p in (carpeta / "locutores" / str(g.id)).iterdir())
    assert guardados == ["consentimiento.wav", *(f"toma-{i}.wav" for i in range(1, 6))]
    # El enlace ya se usó: la página lo dice y no acepta otra tanda.
    assert "Ya recibimos tus grabaciones" in anonimo.get(f"/grabar/{g.token}").text
    r = anonimo.post(f"/grabar/{g.token}", data={"acepto": "si"}, files=_archivos())
    assert r.status_code == 400 and "ya no sirve" in r.json()["error"]


@pytest.mark.parametrize("datos,archivos,mensaje", [
    ({}, _archivos(), "aceptar"),
    ({"acepto": "si"}, _archivos(cuantas=4), "Falta la frase 5"),
    ({"acepto": "si"}, _archivos(toma=1), "muy corta"),
    ({"acepto": "si"}, _archivos(toma=9), "como mucho 7"),
    ({"acepto": "si"}, _archivos(permiso=1), "permiso"),
    ({"acepto": "si"}, {**_archivos(), "toma_2": ("t.wav", b"no es un wav" * 10, "audio/wav")}, "no se pudo leer"),
])
def test_rechaza_lo_que_no_sirve(cliente, Sesion, datos, archivos, mensaje):  # noqa: F811
    _enlace(cliente)
    g = _ultima(Sesion)
    r = TestClient(cliente.app).post(f"/grabar/{g.token}", data=datos, files=archivos)
    assert r.status_code == 400 and mensaje in r.json()["error"]
    assert _ultima(Sesion).estado == "esperando"


def test_un_enlace_vencido_no_sirve(Sesion):  # noqa: F811
    db = Sesion()
    g = locutores.nuevo(db, "Ana Ruiz", "Mujer", None, "x", ahora=datetime.now(timezone.utc) - timedelta(days=8))
    db.commit()
    assert not locutores.vigente(g)
    r = TestClient(__import__("app.main", fromlist=["app"]).app).get(f"/grabar/{g.token}")
    assert "venció" in r.text and "Enviar grabaciones" not in r.text


def _recibida(cliente, Sesion) -> GrabacionVoz:  # noqa: N803, F811
    _enlace(cliente)
    g = _ultima(Sesion)
    assert TestClient(cliente.app).post(f"/grabar/{g.token}", data={"acepto": "si"}, files=_archivos()).json()["ok"]
    return _ultima(Sesion)


def test_revision_escuchar_solo_lo_guardado(cliente, Sesion):  # noqa: F811
    g = _recibida(cliente, Sesion)
    pagina = cliente.get("/voices").text
    assert "Aprobar y clonar" in pagina and f"/voices/locutores/{g.id}/audio/toma-3.wav" in pagina
    r = cliente.get(f"/voices/locutores/{g.id}/audio/toma-3.wav")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert cliente.get(f"/voices/locutores/{g.id}/audio/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert cliente.get(f"/voices/locutores/{g.id}/audio/otra.wav").status_code == 404
    assert TestClient(cliente.app).get(f"/voices/locutores/{g.id}/audio/toma-3.wav").status_code in (303, 401, 403)


def test_aprobar_clona_con_la_toma_elegida(cliente, Sesion, monkeypatch):  # noqa: F811
    g = _recibida(cliente, Sesion)
    llamadas = []

    def clonar(nombre, referencia, archivo, texto, permiso, permiso_archivo, consentimiento):
        llamadas.append((nombre, archivo, texto, consentimiento))
        assert referencia[:4] == b"RIFF" and permiso[:4] == b"RIFF"
        return {"id": "loc9"}

    monkeypatch.setattr(voicestudio, "clonar", clonar)
    r = cliente.post(f"/voices/locutores/{g.id}/aprobar", data={"toma": "2", "nombre": "Pedro"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    nombre, archivo, texto, consentimiento = llamadas[0]
    assert nombre == "Pedro (hombre)" and archivo == "toma-2.wav" and texto == locutores.frases("Hombre")[1]
    assert "Revisado y aprobado por ana@santarosa.com.py" in consentimiento
    g = _ultima(Sesion)
    assert g.estado == "aprobada" and g.perfil_id == "vs:loc9" and g.revisada_por == "ana@santarosa.com.py"
    # Ya aprobada no se vuelve a clonar.
    assert "error=" in cliente.post(f"/voices/locutores/{g.id}/aprobar", data={"toma": "1"}, headers=H).headers["location"]


def test_aprobar_sin_motor_no_clona(cliente, Sesion, monkeypatch):  # noqa: F811
    g = _recibida(cliente, Sesion)
    monkeypatch.setattr(settings, "voicestudio_url", "")
    r = cliente.post(f"/voices/locutores/{g.id}/aprobar", data={"toma": "1"}, headers=H)
    assert "error=" in r.headers["location"] and _ultima(Sesion).estado == "recibida"


def test_rechazar_borra_los_audios(cliente, Sesion, carpeta):  # noqa: F811
    g = _recibida(cliente, Sesion)
    r = cliente.post(f"/voices/locutores/{g.id}/rechazar", data={"motivo": "mucho ruido"}, headers=H)
    assert r.status_code == 303
    g = _ultima(Sesion)
    assert g.estado == "rechazada" and g.motivo == "mucho ruido" and not (carpeta / "locutores" / str(g.id)).exists()
    assert "mucho ruido" in cliente.get("/voices").text
    r = cliente.post(f"/voices/locutores/{g.id}/borrar", headers=H)
    assert r.status_code == 303 and _ultima(Sesion) is None
