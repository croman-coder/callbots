"""Agenda del taller sobre SQLite: horarios libres, bahías, recepciones por franja, tiempos por modelo, reserva,
lo que ve Claude y la pantalla."""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone

import pytest

from app import agenda, fuentes, recepcion
from app.models import Campaign, TallerServicio, TallerSucursal, TallerTurno
from app.services import llm
from tests.test_ficha_agente import H, base, cliente  # noqa: F401

LUNES = date(2026, 10, 26)
# El domingo anterior a la tarde: todo el lunes está en el futuro.
ANTES = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)


def armar(Sesion, **cambios):  # noqa: N803
    db = Sesion()
    suc = TallerSucursal(nombre="Taller Zeballos", direccion="Av. República Argentina 1234",
                         horario={str(d): ["07:00", "11:00"] for d in range(5)}, intervalo_min=60, bahias=2,
                         recepciones_por_franja=2, margen_retiro_min=60, anticipacion_min=120, **cambios)
    s10 = TallerServicio(nombre="Service 10.000 km", palabras="service de los 10 mil", recepcion_min=5, trabajo_min=45,
                         lavado_min=30, calidad_min=5, entrega_min=10)
    duster = TallerServicio(nombre="Service 10.000 km", modelo="Duster", recepcion_min=5, trabajo_min=90,
                            lavado_min=30, calidad_min=5, entrega_min=10)
    chapa = TallerServicio(nombre="Chapería y pintura", recepcion_min=5, trabajo_min=240, calidad_min=5, entrega_min=10)
    db.add_all([suc, s10, duster, chapa])
    db.commit()
    return db, suc, s10, duster, chapa


def horas(lista):
    return [agenda.hora_texto(t) for t, _ in lista]


def test_horarios_libres_y_retiro(base):  # noqa: F811
    db, suc, s10, _, chapa = armar(base)
    assert horas(agenda.libres(db, suc, s10, LUNES, ANTES)) == ["07:00", "08:00", "09:00", "10:00"]
    # La chapería dura 4 h: si arranca después de las 7 no termina antes del cierre de las 11.
    assert horas(agenda.libres(db, suc, chapa, LUNES, ANTES)) == []
    t = agenda.tiempos(suc, s10, datetime.combine(LUNES, time(8, 0), tzinfo=agenda.tz()))
    # 8:00 + 95 min de etapas + 60 de margen = 10:35 → se redondea a la media hora siguiente.
    assert agenda.hora_texto(t.retiro) == "11:00"
    assert agenda.libres(db, suc, s10, LUNES + timedelta(days=5), ANTES) == []  # sábado: cerrado


def test_recepciones_por_franja_y_bahias(base):  # noqa: F811
    db, suc, s10, duster, _ = armar(base)
    ocho = datetime.combine(LUNES, time(8, 0), tzinfo=agenda.tz())
    for nombre in ("Ana", "Beto"):
        agenda.reservar(db, suc=suc, serv=s10, inicio=ocho, cliente=nombre, telefono="0981", momento=ANTES)
    db.commit()
    # Dos autos a las 8 (lo que da abasto la recepción) y las dos bahías ocupadas de 8:05 a 8:50.
    assert "08:00" not in horas(agenda.libres(db, suc, s10, LUNES, ANTES))
    with pytest.raises(agenda.HorarioNoDisponible):
        agenda.reservar(db, suc=suc, serv=s10, inicio=ocho, cliente="Caro", telefono="0982", momento=ANTES)
    # A las 7 el Duster trabaja 90 min (7:05 a 8:35): pisaría a los de las 8 en las dos bahías.
    assert "07:00" not in horas(agenda.libres(db, suc, duster, LUNES, ANTES))
    assert "07:00" in horas(agenda.libres(db, suc, s10, LUNES, ANTES))


def test_anticipacion_para_hoy(base):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    # Lunes 8:30 en Paraguay: con 2 h de anticipación, lo primero es a las 11:00, que ya es el cierre del trabajo.
    ahora = datetime.combine(LUNES, time(8, 30), tzinfo=agenda.tz())
    assert horas(agenda.libres(db, suc, s10, LUNES, ahora)) == []
    suc.anticipacion_min = 30
    assert horas(agenda.libres(db, suc, s10, LUNES, ahora)) == ["09:00", "10:00"]


def test_los_turnos_de_prueba_no_ocupan(base):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    ocho = datetime.combine(LUNES, time(8, 0), tzinfo=agenda.tz())
    for _ in range(3):
        t = agenda.reservar(db, suc=suc, serv=s10, inicio=ocho, cliente="Prueba", telefono="1", origen="chat", momento=ANTES)
        assert t.estado == "prueba"
    db.commit()
    assert "08:00" in horas(agenda.libres(db, suc, s10, LUNES, ANTES))


def test_tiempos_por_modelo(base):  # noqa: F811
    db, _, s10, duster, _ = armar(base)
    assert agenda.servicio_para(db, "Service 10.000 km", "Renault", "Duster 2020").id == duster.id
    assert agenda.servicio_para(db, "service de los 10 mil", "Jetour", "X70").id == s10.id
    assert agenda.servicio_para(db, "lavado de tapizados") is None


@pytest.mark.parametrize("texto,esperado", [
    ("2026-10-27", date(2026, 10, 27)), ("hoy", LUNES), ("mañana", date(2026, 10, 27)),
    ("pasado mañana", date(2026, 10, 28)), ("el jueves", date(2026, 10, 29)), ("lunes", date(2026, 11, 2)),
    ("30/10", date(2026, 10, 30)), ("cualquier día", None),
])
def test_fechas_como_las_dice_claude_o_el_cliente(texto, esperado):
    assert agenda.fecha_de(texto, LUNES) == esperado


def test_consultar_y_reservar_como_lo_pide_el_agente(base):  # noqa: F811
    db, suc, _, _, _ = armar(base)
    texto, turno_id = agenda.consultar(db, {"que": "turnos", "servicio": "Service 10.000 km", "marca": "Renault",
                                            "modelo": "Kardian", "fecha": "2026-10-26", "franja": "mañana"}, momento=ANTES)
    assert turno_id is None and "Libres el lunes 26/10: 07:00, 09:00, 10:00." in texto
    assert "lo retira desde las 10:00" in texto and "trabajo 45" in texto
    sabado, _ = agenda.consultar(db, {"que": "turnos", "servicio": "Service 10.000 km", "fecha": "2026-10-31"}, momento=ANTES)
    assert sabado.startswith("Taller Zeballos.") and "El sábado 31/10 el taller no abre. Libres el lunes 02/11" in sabado
    sin_datos, _ = agenda.consultar(db, {"que": "reservar", "servicio": "Service 10.000 km", "fecha": "2026-10-26",
                                         "hora": "08:00"}, momento=ANTES)
    assert "faltan el nombre y el teléfono" in sin_datos
    ok, turno_id = agenda.consultar(db, {"que": "reservar", "servicio": "Service 10.000 km", "marca": "Renault",
                                         "modelo": "Kardian", "fecha": "2026-10-26", "hora": "8", "nombre": "Laura",
                                         "telefono": "0981 222 333", "km": "10.200"}, origen="telefono", momento=ANTES)
    db.commit()
    turno = db.get(TallerTurno, turno_id)
    assert "Turno reservado" in ok and "Av. República Argentina 1234" in ok and "desde las 11:00" in ok
    assert turno.estado == "agendado" and turno.km == 10200 and turno.cliente_nombre == "Laura"


def test_el_agente_reserva_y_el_turno_queda_atado_al_caso(base, monkeypatch):  # noqa: F811
    db, _, _, _, _ = armar(base)
    respuestas = [
        {"decir": "Ya lo busco en el sistema, un momento.", "accion": "seguir", "motivo": "turno", "nombre": "Laura",
         "consultar": {"que": "reservar", "servicio": "Service 10.000 km", "fecha": "2026-10-26", "hora": "09:00",
                       "nombre": "Laura", "telefono": "0981222333"}},
        {"decir": "Listo, Laura: lunes 26 a las 9 en Taller Zeballos. Lo retira desde las 12. ¿Algo más?", "accion": "seguir"},
    ]

    async def complete(system, user, *, max_tokens=200):
        return json.dumps(respuestas.pop(0), ensure_ascii=False)

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(agenda, "ahora", lambda: ANTES)
    c = Campaign(name="Recepción", modo="recepcion", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48,
                 fuentes={"turnos": True}, areas=[])
    db.add(c)
    db.flush()
    caso = recepcion.nuevo(c, "telefono")
    recepcion.decir(caso, "agente", "Posventa, ¿en qué lo ayudo?")
    db.add(caso)
    db.commit()

    bloque = fuentes.bloque(caso, db)
    assert "«reservar»" in bloque and "Taller Zeballos" in bloque and "Hoy es domingo 25/10" in bloque
    r = asyncio.run(recepcion.turno(caso, "Quiero el service de los 10 mil el lunes a las 9", consultas=bloque))
    assert r["accion"] == "consultar"
    r = asyncio.run(recepcion.continuar(caso, fuentes.consultor(caso)))
    db.commit()
    turno = db.scalars(__import__("sqlalchemy").select(TallerTurno)).one()
    assert turno.caso_id == caso.id and caso.datos["turno"] == str(turno.id) and turno.estado == "agendado"
    assert "Lo retira desde" in r["decir"] or "retira" in r["decir"]


def test_pantalla_de_agenda_y_alta_a_mano(cliente, base, monkeypatch):  # noqa: F811
    db, suc, s10, _, _ = armar(base)
    monkeypatch.setattr(agenda, "ahora", lambda: ANTES)
    r = cliente.post("/agenda/turnos", headers=H, data={
        "sucursal_id": suc.id, "servicio_id": s10.id, "fecha": "2026-10-26", "hora": "09:00",
        "cliente": "Óscar Villalba", "telefono": "0971 444 555", "marca": "Mitsubishi", "modelo": "L200", "km": "20.000"})
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    pagina = cliente.get("/agenda?fecha=2026-10-26").text
    assert "Óscar Villalba" in pagina and "cargado a mano" in pagina and "20.000 km" in pagina
    turno = base().scalars(__import__("sqlalchemy").select(TallerTurno)).one()
    assert cliente.post(f"/agenda/turnos/{turno.id}/estado", data={"estado": "en_taller"}, headers=H).status_code == 303
    assert base().get(TallerTurno, turno.id).estado == "en_taller"
    # El mismo horario dos veces más: la tercera no entra (dos recepciones por franja).
    datos = {"sucursal_id": suc.id, "servicio_id": s10.id, "fecha": "2026-10-26", "hora": "09:00", "cliente": "X", "telefono": "1"}
    cliente.post("/agenda/turnos", headers=H, data=datos)
    r = cliente.post("/agenda/turnos", headers=H, data=datos)
    assert "error=" in r.headers["location"]


def test_configurar_talleres_y_servicios(cliente, base):  # noqa: F811
    armar(base)
    assert "Talleres y servicios" in cliente.get("/agenda/talleres").text
    r = cliente.post("/agenda/talleres", headers=H, data={
        "id": "1", "nombre": "Taller Zeballos", "activa": "1", "desde_5": "07:30", "hasta_5": "11:30", "bahias": "4",
        "intervalo_min": "30", "recepciones_por_franja": "3", "margen_retiro_min": "60", "anticipacion_min": "120"})
    assert r.status_code == 303
    suc = base().get(TallerSucursal, 1)
    assert suc.horario == {"5": ["07:30", "11:30"]} and suc.bahias == 4
    r = cliente.post("/agenda/servicios", headers=H, data={
        "sv_id": ["1", ""], "sv_nombre": ["", "Alineación y balanceo"], "sv_palabras": ["", ""], "sv_marca": ["", ""],
        "sv_modelo": ["", ""], "sv_recepcion": ["5", "5"], "sv_trabajo": ["45", "40"], "sv_lavado": ["30", "0"],
        "sv_calidad": ["5", "5"], "sv_entrega": ["10", "10"]})
    db = base()
    assert db.get(TallerServicio, 1).activo is False
    assert any(s.nombre == "Alineación y balanceo" for s in agenda.servicios_activos(db))
