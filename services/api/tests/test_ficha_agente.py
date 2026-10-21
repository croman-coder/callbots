"""Ficha del agente sobre una base SQLite en memoria: pestañas, guardado parcial, activar, IA y la
prueba desde el navegador contra un voice-agent falso que habla AudioSocket."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.websockets import WebSocketDisconnect

from app import agentes, db as dbmod, main, models, sso
from app.config import settings
from app.models import Campaign, PruebaAgente, Question, QuestionType
from app.routers import agente


@pytest.fixture()
def base(monkeypatch):
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(motor)
    Sesion = sessionmaker(motor, expire_on_commit=False)

    def get_db():
        s = Sesion()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[dbmod.get_db] = get_db
    monkeypatch.setattr(dbmod, "SessionLocal", Sesion)
    s = Sesion()
    c = Campaign(name="Posventa", description="Encuesta de satisfacción", bitrix_entity_type_id=2,
                 trigger_field="campo", delay_hours=48, is_active=False, empresa_nombre="Santa Rosa")
    s.add(c)
    s.commit()
    yield Sesion
    main.app.dependency_overrides.pop(dbmod.get_db, None)


def _con_pregunta(Sesion):
    s = Sesion()
    s.add(Question(campaign_id=1, position=1, text="¿Del cero al diez?", qtype=QuestionType.SCALE_1_10))
    s.commit()


def _cookie(rol="admin"):
    return sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", rol, "crm"), settings.sso_secret)


@pytest.fixture()
def cliente(base):
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, _cookie())
    return c


H = {"Sec-Fetch-Site": "same-origin"}


@pytest.mark.parametrize("tab,texto", [
    ("contexto", "Contexto de la empresa"), ("probar", "Iniciar llamada web"), ("configuracion", "Configuración de voz"),
    ("analisis", "Objetivo de la llamada"), ("registro", "Llamada exitosa"), ("metrica", "Embudo de contacto"),
    ("canales", "WhatsApp Business"),
])
def test_cada_pestana_se_ve(cliente, tab, texto):
    r = cliente.get(f"/campaigns/1?tab={tab}")
    assert r.status_code == 200 and texto in r.text


def test_un_agente_que_no_existe_da_404(cliente):
    assert cliente.get("/campaigns/99").status_code == 404


def test_guardar_toca_solo_lo_que_manda_la_pestana(cliente, base):
    r = cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "empresa_descripcion": "Concesionaria."}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"] and "tab=contexto" in r.headers["location"]
    c = base().get(Campaign, 1)
    assert c.empresa_descripcion == "Concesionaria." and c.empresa_nombre == "Santa Rosa" and c.name == "Posventa"


@pytest.mark.parametrize("datos,motivo", [
    ({"name": "  "}, "nombre"),
    ({"dias_enviados": "1"}, "d%C3%ADa"),
    ({"call_window_start": "20:00", "call_window_end": "09:00"}, "horario"),
    ({"intro_script": ""}, "guion"),
    ({"voice_profile_id": "../../etc"}, "Voz"),
])
def test_guardar_rechaza_lo_invalido(cliente, base, datos, motivo):
    r = cliente.post("/campaigns/1/guardar", data={"tab": "configuracion", **datos}, headers=H)
    assert r.status_code == 303 and "error=" in r.headers["location"]


def test_hora_mal_escrita_da_400(cliente):
    assert cliente.post("/campaigns/1/guardar", data={"call_window_start": "25:99"}, headers=H).status_code == 400


def test_dias_y_topes(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"dias_enviados": "1", "dias": ["0", "2", "9"], "max_attempts": "50",
                                               "voice_speed": "9"}, headers=H)
    c = base().get(Campaign, 1)
    assert c.call_window_days == "0,2" and c.max_attempts == 10 and c.voice_speed == 2.0


def test_no_se_activa_sin_preguntas(cliente, base):
    r = cliente.post("/campaigns/1/estado", data={"activo": "true"}, headers=H)
    assert "error=" in r.headers["location"] and not base().get(Campaign, 1).is_active
    _con_pregunta(base)
    cliente.post("/campaigns/1/estado", data={"activo": "true"}, headers=H)
    assert base().get(Campaign, 1).is_active


def test_instrucciones_con_ia_usa_lo_que_esta_en_pantalla(cliente, monkeypatch):
    visto = {}

    async def generar(**k):
        visto.update(k)
        return "# Identidad\nSos el agente."

    monkeypatch.setattr(agentes, "generar_instrucciones", generar)
    agentes._pedidos.clear()
    r = cliente.post("/campaigns/1/instrucciones", data={"name": "Nuevo nombre", "actual": ""}, headers=H)
    assert r.json()["markdown"].startswith("# Identidad") and visto["nombre"] == "Nuevo nombre"
    assert visto["empresa"] == "Santa Rosa"  # lo que no vino del formulario sale de lo guardado


def test_valorar_una_prueba(cliente, base):
    s = base()
    s.add(PruebaAgente(campaign_id=1, usuario="ana", transcripcion=[]))
    s.commit()
    assert cliente.post("/campaigns/1/pruebas/1/valoracion", data={"valoracion": "bien"}, headers=H).json()["ok"]
    assert base().get(PruebaAgente, 1).valoracion == "bien"
    assert cliente.post("/campaigns/2/pruebas/1/valoracion", data={"valoracion": "bien"}, headers=H).status_code == 404


# ------------------------------------------------------------------ prueba desde el navegador
ORIGEN = {"origin": "http://testserver"}


def test_ws_sin_sesion_se_cierra(cliente):
    c = TestClient(main.app)
    with pytest.raises(WebSocketDisconnect) as e:
        with c.websocket_connect("/ws/prueba/1", headers=ORIGEN) as ws:
            ws.receive_text()
    assert e.value.code == 1008


def test_ws_desde_otro_sitio_se_cierra(cliente):
    with pytest.raises(WebSocketDisconnect) as e:
        with cliente.websocket_connect("/ws/prueba/1", headers={"origin": "https://malo.example"}) as ws:
            ws.receive_text()
    assert e.value.code == 1008


def test_ws_sin_preguntas_avisa(cliente):
    with cliente.websocket_connect("/ws/prueba/1", headers=ORIGEN) as ws:
        assert json.loads(ws.receive_text())["tipo"] == "error"


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ws_sin_voice_agent_avisa(cliente, base, monkeypatch):
    _con_pregunta(base)
    monkeypatch.setattr(settings, "voice_agent_host", "127.0.0.1")
    monkeypatch.setattr(settings, "voice_agent_port", _puerto_libre())
    with cliente.websocket_connect("/ws/prueba/1", headers=ORIGEN) as ws:
        m = json.loads(ws.receive_text())
    assert m["tipo"] == "error" and "no está disponible" in m["texto"]


def test_ws_puentea_audio_y_transcripcion_con_el_voice_agent(cliente, base, monkeypatch):
    _con_pregunta(base)
    puerto = _puerto_libre()
    recibido = {}

    async def agente_falso(reader, writer):
        def trama(t, p=b""):
            return bytes([t]) + len(p).to_bytes(2, "big") + p
        cab = await reader.readexactly(3)
        recibido["uuid"] = uuid.UUID(bytes=await reader.readexactly(int.from_bytes(cab[1:3], "big")))
        writer.write(trama(0x20, json.dumps({"rol": "agente", "texto": "Hola"}).encode()))
        writer.write(trama(0x10, b"\x01\x00" * 160))
        await writer.drain()
        cab = await reader.readexactly(3)
        recibido["audio"] = await reader.readexactly(int.from_bytes(cab[1:3], "big"))
        writer.write(trama(0x20, json.dumps({"rol": "cliente", "texto": "Sí"}).encode()))
        writer.write(trama(0x00))
        await writer.drain()

    listo = threading.Event()

    def servir():
        async def m():
            srv = await asyncio.start_server(agente_falso, "127.0.0.1", puerto)
            listo.set()
            async with srv:
                await asyncio.sleep(5)
        asyncio.run(m())

    threading.Thread(target=servir, daemon=True).start()
    listo.wait(3)
    monkeypatch.setattr(settings, "voice_agent_host", "127.0.0.1")
    monkeypatch.setattr(settings, "voice_agent_port", puerto)

    with cliente.websocket_connect("/ws/prueba/1", headers=ORIGEN) as ws:
        assert json.loads(ws.receive_text())["tipo"] == "inicio"
        assert json.loads(ws.receive_text()) == {"tipo": "texto", "rol": "agente", "texto": "Hola"}
        assert ws.receive_bytes() == b"\x01\x00" * 160
        ws.send_bytes(b"\x02\x00" * 200)  # 400 bytes: sale una trama de 320 y el resto espera
        assert json.loads(ws.receive_text())["rol"] == "cliente"
        assert json.loads(ws.receive_text())["tipo"] == "fin"

    assert recibido["uuid"].int == 1  # la sesión de prueba del agente 1: el voice-agent no guarda nada
    assert recibido["audio"] == b"\x02\x00" * 160
    prueba = base().get(PruebaAgente, 1)
    assert [l["texto"] for l in prueba.transcripcion] == ["Hola", "Sí"] and prueba.usuario == "ana@santarosa.com.py"


def test_vaciar_un_texto_lo_borra(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"tab": "analisis", "analysis_prompt": "Algo"}, headers=H)
    cliente.post("/campaigns/1/guardar", data={"tab": "analisis", "analysis_prompt": ""}, headers=H)
    assert base().get(Campaign, 1).analysis_prompt is None
