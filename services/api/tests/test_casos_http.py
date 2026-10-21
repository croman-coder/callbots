"""Recepción por HTTP sobre SQLite: crear el agente con la plantilla, guardar áreas, chat de prueba, bandeja y caso,
y los endpoints que usa el voice-agent."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main, recepcion
from app.config import settings
from app.models import Campaign, Caso
from app.services import llm
from tests.test_ficha_agente import H, _cookie, base  # noqa: F401

INTERNO = {"X-Callbot-Token": settings.internal_token}


@pytest.fixture()
def cliente(base):  # noqa: F811
    from app import sso

    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, _cookie())
    return c


@pytest.fixture()
def claude(monkeypatch):
    """Claude de mentira: pide el nombre, después deriva a Repuestos y después se despide."""
    async def complete(system, user, *, max_tokens=200):
        vueltas = user.count("Cliente:")
        if vueltas <= 1:
            return json.dumps({"decir": "¿Me dice su nombre?", "accion": "seguir", "area": "repuestos", "motivo": "reclamo"})
        if vueltas == 2:
            return json.dumps({"decir": "Se lo paso a Repuestos: le responden en las próximas 24 horas. ¿Algo más?",
                               "accion": "derivar", "area": "repuestos", "nombre": "Ramón", "resumen": "Espejo que no llega"})
        return json.dumps({"decir": "Que tenga buen día.", "accion": "terminar"})

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)


def _agente(cliente) -> int:
    r = cliente.post("/campaigns", data={"plantilla": "recepcion", "name": "Recepción posventa"}, headers=H)
    assert r.status_code == 303
    return int(r.headers["location"].split("/campaigns/")[1].split("?")[0])


def test_la_plantilla_arma_un_agente_de_recepcion(cliente, base):  # noqa: F811
    cid = _agente(cliente)
    c = base().get(Campaign, cid)
    assert c.modo == "recepcion" and [a["clave"] for a in c.areas][:2] == ["repuestos", "taller_y_servicio"]
    assert c.limites == recepcion.LIMITES_POR_DEFECTO and "asistente virtual" in c.intro_script
    pagina = cliente.get(f"/campaigns/{cid}?tab=configuracion").text
    assert "Áreas y responsables" in pagina and "Lo que no puede decir ni prometer" in pagina
    assert 'name="dias_enviados"' not in pagina and 'id="preguntas"' not in pagina
    assert "Chat de texto" in cliente.get(f"/campaigns/{cid}?tab=probar").text


def test_guardar_areas_y_limites(cliente, base):  # noqa: F811
    cid = _agente(cliente)
    r = cliente.post(f"/campaigns/{cid}/guardar", headers=H, data={
        "tab": "configuracion", "ar_enviado": "1",
        "ar_nombre": ["Repuestos", ""], "ar_atiende": ["pedidos", ""], "ar_responsable": ["Valentina Ortiz", ""],
        "ar_correo": ["repuestos@santarosa.com.py", ""], "ar_plazo": ["24", "12"], "limites": "No dé precios.",
    })
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    c = base().get(Campaign, cid)
    assert [a["responsable"] for a in c.areas] == ["Valentina Ortiz"] and c.limites == "No dé precios."


def test_chat_de_prueba_deriva_y_aparece_en_la_bandeja_solo_con_pruebas(cliente, base, claude):  # noqa: F811
    cid = _agente(cliente)
    inicio = cliente.post(f"/campaigns/{cid}/recepcion/chat", data={"caso_id": "", "texto": ""}, headers=H).json()
    caso_id = str(inicio["caso_id"])
    assert inicio["lineas"][0]["quien"] == "agente"
    cliente.post(f"/campaigns/{cid}/recepcion/chat", data={"caso_id": caso_id, "texto": "Encargué un espejo"}, headers=H)
    j = cliente.post(f"/campaigns/{cid}/recepcion/chat", data={"caso_id": caso_id, "texto": "Ramón"}, headers=H).json()
    assert j["area"] == "Repuestos" and j["plazo_horas"] == 24 and not j["fin"]

    assert "Ramón" not in cliente.get("/casos").text  # las pruebas no ensucian la bandeja
    assert "Ramón" in cliente.get("/casos?pruebas=1&filtro=todos").text
    detalle = cliente.get(f"/casos/{caso_id}").text
    assert "Espejo que no llega" in detalle and "Encargué un espejo" in detalle


def test_responder_un_caso_desde_el_detalle(cliente, base, claude):  # noqa: F811
    cid = _agente(cliente)
    caso_id = cliente.post(f"/campaigns/{cid}/recepcion/chat", data={"texto": ""}, headers=H).json()["caso_id"]
    for texto in ("Encargué un espejo", "Ramón"):
        cliente.post(f"/campaigns/{cid}/recepcion/chat", data={"caso_id": str(caso_id), "texto": texto}, headers=H)
    r = cliente.post(f"/casos/{caso_id}/responder", data={"respuesta": "Llega el 15/12, se le avisó."}, headers=H)
    assert r.status_code == 303
    caso = base().get(Caso, caso_id)
    assert caso.estado == "respondido" and caso.eventos[-1]["por"] == "ana@santarosa.com.py"


def test_el_voice_agent_abre_conversa_y_cierra_el_caso(cliente, base, claude):  # noqa: F811
    cid = _agente(cliente)
    sesion = f"00000000-0000-0000-0000-{cid:012x}"  # la prueba de voz desde el navegador
    guion = cliente.get(f"/internal/sessions/{sesion}/script", headers=INTERNO).json()
    assert guion["modo"] == "recepcion" and guion["frases"]["sigue_ahi"] == recepcion.FRASE_SIGUE_AHI
    caso_id = cliente.post(f"/internal/sessions/{sesion}/recepcion", json={}, headers=INTERNO).json()["caso_id"]
    r = cliente.post(f"/internal/casos/{caso_id}/turno", json={"texto": "Encargué un espejo"}, headers=INTERNO).json()
    assert r == {"decir": "¿Me dice su nombre?", "accion": "seguir"}
    fin = cliente.post(f"/internal/casos/{caso_id}/fin", json={"causa": "cortó"}, headers=INTERNO).json()
    caso = base().get(Caso, caso_id)
    assert fin["estado"] == "abierto" and caso.origen == "prueba" and caso.area == "repuestos"


def test_una_encuesta_no_acepta_la_recepcion(cliente):
    assert cliente.post("/campaigns/1/recepcion/chat", data={"texto": "hola"}, headers=H).status_code == 409
