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
    sesion = uuid.uuid4()
    r = cliente.post("/internal/entrantes", data={"uuid": str(sesion), "de": "0981222333", "a": "400022"}, headers=INTERNO)
    assert r.status_code == 200 and r.text == "ok"
    caso = base().scalars(__import__("sqlalchemy").select(Caso)).one()
    assert caso.campaign_id == cid and caso.telefono == "0981222333" and caso.origen == "telefono"
    assert caso.conversacion[0] == {**caso.conversacion[0], "quien": "agente", "texto": "Posventa, ¿en qué lo ayudo?"}

    guion = cliente.get(f"/internal/sessions/{sesion}/script", headers=INTERNO).json()
    assert guion["modo"] == "recepcion" and guion["demo"] is False and guion["questions"] == []
    # El voice-agent pide abrir el caso: ya existe, no se duplica.
    assert cliente.post(f"/internal/sessions/{sesion}/recepcion", json={}, headers=INTERNO).json() == {"caso_id": caso.id}
    assert cliente.post(f"/internal/sessions/{sesion}/started", json={}, headers=INTERNO).json() == {"entrante": True}
    assert cliente.post(f"/internal/sessions/{sesion}/transfer", headers=INTERNO).json()["ok"] is False  # sin asesor
    # Asterisk reintenta con la misma sesión: tampoco se duplica.
    cliente.post("/internal/entrantes", data={"uuid": str(sesion), "de": "0981222333", "a": "400022"}, headers=INTERNO)
    assert len(base().scalars(__import__("sqlalchemy").select(Caso)).all()) == 1


def test_sin_agente_contesta_no_y_sin_token_no_entra(cliente, base):  # noqa: F811
    r = cliente.post("/internal/entrantes", data={"uuid": str(uuid.uuid4()), "de": "1", "a": "2"}, headers=INTERNO)
    assert r.text == "no"
    r = cliente.post("/internal/entrantes", data={"uuid": str(uuid.uuid4()), "de": "1", "a": "2"})
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
