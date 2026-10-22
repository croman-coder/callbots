"""Línea entrante: qué agente atiende cada número, lo que hace la API cuando Asterisk avisa que entró una llamada,
y la pestaña Canales."""
from __future__ import annotations

import uuid

from app import entrantes
from app.config import settings
from app.models import Campaign, Caso
from tests.test_casos_http import _agente, cliente  # noqa: F401
from tests.test_ficha_agente import H, base  # noqa: F401

INTERNO = {"X-Callbot-Token": settings.internal_token}


def test_numeros_sin_prefijos():
    assert entrantes.numeros("0981 123 456\n+595 21 555 666, 9100") == ["981123456", "21555666", "9100"]
    assert entrantes.numeros("") == []


def _recepcion(Sesion, nombre, entrante=True, numeros=None):  # noqa: N803
    s = Sesion()
    c = Campaign(name=nombre, modo="recepcion", entrante=entrante, numeros_entrantes=numeros, bitrix_entity_type_id=2,
                 trigger_field="x", delay_hours=48, intro_script="Posventa, ¿en qué lo ayudo?", areas=[])
    s.add(c)
    s.commit()
    return c.id


def test_que_agente_atiende_cada_numero(base):  # noqa: F811
    general = _recepcion(base, "General")
    taller = _recepcion(base, "Taller", numeros="021 555 666")
    _recepcion(base, "Apagado", entrante=False, numeros="9100")
    db = base()
    assert entrantes.agente_para(db, "+59521555666").id == taller  # el que nombra el número gana
    assert entrantes.agente_para(db, "400022").id == general  # el resto, el que atiende cualquiera
    assert entrantes.agente_para(db, "9100").id == general  # el apagado no atiende


def test_ningun_agente_atiende(base):  # noqa: F811
    _recepcion(base, "Solo el 9100", numeros="9100")
    db = base()
    assert entrantes.abrir(db, uuid.uuid4(), "0981", "400022") is None


def test_asterisk_avisa_y_el_voice_agent_atiende(cliente, base):  # noqa: F811
    cid = _recepcion(base, "Recepción")
    r = cliente.post("/internal/entrantes", data={"de": "0981222333", "a": "400022"}, headers=INTERNO)
    assert r.status_code == 200 and r.text.startswith("ok:")
    sesion = uuid.UUID(r.text.split(":", 1)[1])  # la sesión la crea la API: Asterisk la usa para el AudioSocket
    caso = base().scalars(__import__("sqlalchemy").select(Caso)).one()
    assert caso.campaign_id == cid and caso.telefono == "0981222333" and caso.origen == "telefono"
    assert caso.conversacion[0] == {**caso.conversacion[0], "quien": "agente", "texto": "Posventa, ¿en qué lo ayudo?"}

    guion = cliente.get(f"/internal/sessions/{sesion}/script", headers=INTERNO).json()
    assert guion["modo"] == "recepcion" and guion["demo"] is False and guion["questions"] == []
    # El voice-agent pide abrir el caso: ya existe, no se duplica.
    assert cliente.post(f"/internal/sessions/{sesion}/recepcion", json={}, headers=INTERNO).json() == {"caso_id": caso.id}
    assert cliente.post(f"/internal/sessions/{sesion}/started", json={}, headers=INTERNO).json() == {"entrante": True}
    assert cliente.post(f"/internal/sessions/{sesion}/transfer", headers=INTERNO).json()["ok"] is False  # sin asesor
    assert caso.session_uuid == sesion


def test_sin_agente_contesta_no_y_sin_token_no_entra(cliente, base):  # noqa: F811
    r = cliente.post("/internal/entrantes", data={"de": "1", "a": "2"}, headers=INTERNO)
    assert r.text == "no"
    r = cliente.post("/internal/entrantes", data={"de": "1", "a": "2"})
    assert r.status_code in (401, 403)


def test_pestana_canales_prende_la_linea(cliente, base):  # noqa: F811
    cid = _agente(cliente)
    pagina = cliente.get(f"/campaigns/{cid}?tab=canales").text
    assert "Atender llamadas entrantes" in pagina and "9100" in pagina
    r = cliente.post(f"/campaigns/{cid}/entrantes", data={"entrante": "1", "numeros_entrantes": "9100\n021 555 666"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    c = base().get(Campaign, cid)
    assert c.entrante and c.numeros_entrantes == "9100\n21555666"
    assert "Atender llamadas entrantes" not in cliente.get("/campaigns/1?tab=canales").text  # una encuesta no atiende
    assert cliente.post("/campaigns/1/entrantes", data={"entrante": "1"}, headers=H).headers["location"].count("error=") == 1


def test_la_extension_de_prueba_abre_casos_de_prueba(cliente, base):  # noqa: F811
    _recepcion(base, "Recepción", numeros="9100\n021 555 666")
    assert entrantes.es_de_prueba("9100") and not entrantes.es_de_prueba("021 555 666") and not entrantes.es_de_prueba("")
    r = cliente.post("/internal/entrantes", data={"de": "", "a": "9100"}, headers=INTERNO)
    assert r.text.startswith("ok:")
    caso = base().scalars(__import__("sqlalchemy").select(Caso)).one()
    # Lo de la extensión interna es una prueba: no ocupa la agenda, no avisa al área ni cuenta en la bandeja.
    assert caso.origen == "prueba" and any("extensión de prueba" in e["texto"] for e in caso.eventos)
    sesion = uuid.UUID(r.text.split(":", 1)[1])
    guion = cliente.get(f"/internal/sessions/{sesion}/script", headers=INTERNO).json()
    assert guion["modo"] == "recepcion" and guion["demo"] is False


def test_whisper_conoce_los_talleres_de_la_agenda(cliente, base):  # noqa: F811
    from app.models import TallerSucursal

    cid = _recepcion(base, "Recepción", numeros="9100")
    s = base()
    s.get(Campaign, cid).fuentes = {"turnos": True}
    s.get(Campaign, cid).vocabulario = "Kardian"
    s.add(TallerSucursal(nombre="Taller Zeballos", horario={"0": ["07:00", "18:00"]}, intervalo_min=60, bahias=3))
    s.commit()
    sesion = uuid.UUID(cliente.post("/internal/entrantes", data={"de": "", "a": "9100"}, headers=INTERNO).text[3:])
    vocab = cliente.get(f"/internal/sessions/{sesion}/script", headers=INTERNO).json()["vocabulario"]
    assert vocab[:2] == ["Kardian", "Taller Zeballos"]  # sin esto Whisper escribía «Ceballos»
