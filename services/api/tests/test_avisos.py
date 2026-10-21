"""Avisos por WhatsApp: qué pasa con cada aviso según la configuración, lo que se le manda a YCloud, la firma y los
estados del webhook, los recordatorios y los eventos (reserva, vehículo listo, caso derivado)."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app import agenda, avisos, recepcion
from app.config import settings
from app.models import AvisoConfig, Campaign, WhatsappEnvio
from tests.test_agenda import ANTES, LUNES, armar
from tests.test_ficha_agente import H, base, cliente  # noqa: F401


@pytest.fixture()
def conectado(monkeypatch):
    monkeypatch.setattr(settings, "ycloud_api_key", "clave-de-prueba")
    monkeypatch.setattr(settings, "ycloud_numero", "+595 981 000 111")
    monkeypatch.setattr(settings, "ycloud_webhook_secreto", "secreto")
    encolados: list[int] = []
    monkeypatch.setattr(avisos, "despachar", lambda envio_id: encolados.append(envio_id))
    return encolados


def prender(db, *eventos):
    for e in eventos:
        avisos.config(db, e).activo = True
    db.commit()


@pytest.mark.parametrize("entrada,salida", [
    ("0981 123 456", "+595981123456"), ("+595 981 123 456", "+595981123456"), ("981123456", "+595981123456"),
    ("+54 9 11 5555 6666", "+5491155556666"), ("123", None), ("", None),
])
def test_telefono_en_formato_internacional(entrada, salida):
    assert avisos.e164(entrada) == salida


def test_que_pasa_con_un_aviso_segun_la_configuracion(base, monkeypatch):  # noqa: F811
    db = base()
    variables = ["Laura", "Taller Zeballos", "lunes 26/10 a las 14:00", "Service", "desde las 17:00"]
    assert avisos.registrar(db, "turno_confirmado", telefono="0981", nombre="L", variables=variables).estado == "apagado"
    prender(db, "turno_confirmado")
    assert avisos.registrar(db, "turno_confirmado", telefono="0981 222 333", nombre="L", variables=variables).estado == "sin_configurar"
    monkeypatch.setattr(settings, "ycloud_api_key", "k")
    monkeypatch.setattr(settings, "ycloud_numero", "+595981000111")
    assert avisos.registrar(db, "turno_confirmado", telefono="", nombre="L", variables=variables).estado == "sin_telefono"
    assert avisos.registrar(db, "turno_confirmado", telefono="0981 222 333", nombre="L", variables=variables).estado == "pendiente"


def test_lo_que_se_le_manda_a_ycloud(base, conectado):  # noqa: F811
    db = base()
    prender(db, "vehiculo_listo")
    envio = avisos.registrar(db, "vehiculo_listo", telefono="0981 222 333", nombre="Laura",
                             variables=["Laura", "Jetour X70", "Taller Zeballos"])
    pedidos = []

    def responder(request: httpx.Request) -> httpx.Response:
        pedidos.append(request)
        return httpx.Response(200, json={"id": "wamid.abc", "status": "accepted"})

    avisos.enviar(envio, httpx.Client(transport=httpx.MockTransport(responder)))
    cuerpo = json.loads(pedidos[0].content)
    assert pedidos[0].url == "https://api.ycloud.com/v2/whatsapp/messages"
    assert pedidos[0].headers["X-API-Key"] == "clave-de-prueba"
    assert cuerpo["from"] == "+595981000111" and cuerpo["to"] == "+595981222333" and cuerpo["type"] == "template"
    assert cuerpo["template"]["name"] == "callbot_vehiculo_listo" and cuerpo["template"]["language"] == {"code": "es"}
    assert [p["text"] for p in cuerpo["template"]["components"][0]["parameters"]] == ["Laura", "Jetour X70", "Taller Zeballos"]
    assert envio.estado == "enviado" and envio.ycloud_id == "wamid.abc" and cuerpo["externalId"] == f"callbot-{envio.id}"


def test_un_rechazo_de_ycloud_queda_como_fallo(base, conectado):  # noqa: F811
    db = base()
    envio = avisos.registrar(db, "vehiculo_listo", telefono="0981 222 333", nombre="L", variables=["a", "b", "c"], prueba=True)
    avisos.enviar(envio, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, text="template not found"))))
    assert envio.estado == "fallo" and "400" in envio.error and "template not found" in envio.error


def test_firma_del_webhook():
    cuerpo = b'{"type":"whatsapp.message.updated"}'
    t = "1792600000"
    s = hmac.new(b"secreto", f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    assert avisos.firma_valida(f"t={t},s={s}", cuerpo, "secreto", ahora=1792600010)
    assert not avisos.firma_valida(f"t={t},s={s}", cuerpo + b" ", "secreto", ahora=1792600010)  # cuerpo cambiado
    assert not avisos.firma_valida(f"t={t},s={s}", cuerpo, "otro", ahora=1792600010)
    assert not avisos.firma_valida(f"t={t},s={s}", cuerpo, "secreto", ahora=1792600000 + 3600)  # vieja
    assert not avisos.firma_valida(None, cuerpo, "secreto")


def _webhook(cliente, cuerpo: dict, secreto="secreto"):  # noqa: F811
    crudo = json.dumps(cuerpo).encode()
    t = str(int(datetime.now(timezone.utc).timestamp()))
    s = hmac.new(secreto.encode(), f"{t}.".encode() + crudo, hashlib.sha256).hexdigest()
    return cliente.post("/webhooks/ycloud", content=crudo, headers={"YCloud-Signature": f"t={t},s={s}",
                                                                    "Content-Type": "application/json"})


def test_el_webhook_mueve_el_estado_solo_hacia_adelante(cliente, base, conectado):  # noqa: F811
    db = base()
    envio = avisos.registrar(db, "vehiculo_listo", telefono="0981 222 333", nombre="L", variables=["a", "b", "c"], prueba=True)
    envio.estado, envio.ycloud_id = "enviado", "wamid.1"
    db.commit()
    cliente.cookies.clear()  # YCloud no tiene sesión
    estado = lambda: base().get(WhatsappEnvio, envio.id).estado  # noqa: E731
    msg = lambda st, **kw: {"type": "whatsapp.message.updated", "whatsappMessage": {"id": "wamid.1", "status": st, **kw}}  # noqa: E731
    assert _webhook(cliente, msg("read")).status_code == 200 and estado() == "leido"
    _webhook(cliente, msg("delivered"))  # llega tarde: no vuelve atrás
    assert estado() == "leido"
    assert _webhook(cliente, msg("failed"), secreto="falso").status_code == 401 and estado() == "leido"


def test_fuera_de_la_ventana_queda_el_motivo(cliente, base, conectado):  # noqa: F811
    db = base()
    envio = avisos.registrar(db, "vehiculo_listo", telefono="0981 222 333", nombre="L", variables=["a", "b", "c"], prueba=True)
    envio.estado, envio.ycloud_id = "enviado", "wamid.2"
    db.commit()
    cliente.cookies.clear()
    _webhook(cliente, {"type": "whatsapp.message.updated", "whatsappMessage": {
        "id": "wamid.2", "status": "failed", "errorCode": "131047", "errorMessage": "Re-engagement message"}})
    envio = base().get(WhatsappEnvio, envio.id)
    assert envio.estado == "fallo" and envio.error == "131047: Re-engagement message"


def test_reservar_avisa_al_cliente_y_listo_tambien(cliente, base, conectado, monkeypatch):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    prender(db, "turno_confirmado", "vehiculo_listo")
    monkeypatch.setattr(agenda, "ahora", lambda: ANTES)
    r = cliente.post("/agenda/turnos", headers=H, data={
        "sucursal_id": suc.id, "servicio_id": s10.id, "fecha": "2026-10-26", "hora": "08:00",
        "cliente": "laura benítez", "telefono": "0991 666 777", "marca": "Jetour", "modelo": "X70"})
    assert r.status_code == 303
    envio = base().scalars(select(WhatsappEnvio)).one()
    assert envio.evento == "turno_confirmado" and envio.destino == "+595991666777" and conectado == [envio.id]
    assert envio.variables == ["Laura", "Taller Zeballos (Av. República Argentina 1234)", "lunes 26/10 a las 08:00",
                               "Service 10.000 km", "desde las 11:00"]
    cliente.post(f"/agenda/turnos/{envio.turno_id}/estado", data={"estado": "listo"}, headers=H)
    listo = base().scalars(select(WhatsappEnvio).where(WhatsappEnvio.evento == "vehiculo_listo")).one()
    assert listo.variables == ["Laura", "Jetour X70", "Taller Zeballos (Av. República Argentina 1234)"]


def test_turnos_de_prueba_no_avisan(base, conectado):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    prender(db, "turno_confirmado")
    t = agenda.reservar(db, suc=suc, serv=s10, inicio=datetime.combine(LUNES, time(8), tzinfo=agenda.tz()),
                        cliente="X", telefono="0981 1", origen="chat", momento=ANTES)
    db.commit()
    assert avisos.por_turno_reservado(t.id) is None and conectado == []


def test_recordatorios_el_dia_anterior_desde_la_hora_elegida(base, conectado):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    prender(db, "turno_recordatorio")
    for h in (8, 9):
        agenda.reservar(db, suc=suc, serv=s10, inicio=datetime.combine(LUNES, time(h), tzinfo=agenda.tz()),
                        cliente="Ana", telefono="0981 222 333", momento=ANTES)
    db.commit()
    domingo = lambda h: datetime.combine(LUNES - timedelta(days=1), time(h), tzinfo=agenda.tz())  # noqa: E731
    assert avisos.recordatorios(domingo(16)) == 0  # todavía no son las 17
    assert avisos.recordatorios(domingo(17)) == 2
    assert avisos.recordatorios(domingo(18)) == 0  # no se repiten
    assert len(conectado) == 2


def test_caso_derivado_avisa_al_responsable_si_su_area_tiene_whatsapp(base, conectado):  # noqa: F811
    db = base()
    prender(db, "caso_derivado")
    c = Campaign(name="Recepción", modo="recepcion", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48,
                 areas=recepcion.normalizar_areas([{"nombre": "Repuestos", "responsable": "Valentina", "whatsapp": "0981 555 444"},
                                                   {"nombre": "Garantía"}]))
    db.add(c)
    db.flush()
    caso = recepcion.nuevo(c, "telefono")
    caso.nombre, caso.resumen = "Ramón", "Espejo que no llega"
    recepcion.decir(caso, "cliente", "hola")
    recepcion.derivar(caso, recepcion.area_por_clave(c, "repuestos"))
    sin = recepcion.nuevo(c, "telefono")
    recepcion.decir(sin, "cliente", "hola")
    recepcion.derivar(sin, recepcion.area_por_clave(c, "garantia"))
    db.add_all([caso, sin])
    db.commit()
    envio_id = avisos.por_caso_derivado(caso.id)
    envio = base().get(WhatsappEnvio, envio_id)
    assert envio.destino == "+595981555444" and envio.variables[:3] == ["Repuestos", "Espejo que no llega", "Ramón"]
    assert envio.variables[4].endswith(f"/casos/{caso.id}")
    assert avisos.por_caso_derivado(sin.id) is None  # Garantía no tiene WhatsApp


def test_pantalla_de_avisos(cliente, base):  # noqa: F811
    pagina = cliente.get("/avisos").text
    assert "sin conectar" in pagina and "poner-clave-ycloud.sh" in pagina and "Recordatorio del turno" in pagina
    assert "Hola Laura, su turno en Taller Zeballos quedó agendado para el lunes 26/10 a las 14:00." in pagina
    r = cliente.post("/avisos/config", headers=H, data={"activo_vehiculo_listo": "1", "plantilla_vehiculo_listo": "Mi_Plantilla",
                                                        "idioma_vehiculo_listo": "es", "hora_turno_recordatorio": "18:30"})
    assert r.status_code == 303
    db = base()
    assert db.get(AvisoConfig, "vehiculo_listo").activo and db.get(AvisoConfig, "vehiculo_listo").plantilla == "mi_plantilla"
    assert db.get(AvisoConfig, "turno_recordatorio").hora == "18:30" and not db.get(AvisoConfig, "turno_recordatorio").activo
