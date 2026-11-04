"""Editar el nombre y el teléfono de un destinatario desde la cola de llamadas (pedido del 4/11/2026).

Antes solo se podía llamar o reagendar: un teléfono mal cargado obligaba a borrarlo a mano de la base y volver a
cargar la lista. El teléfono se normaliza igual que al cargar (`0981 123 456` = `+595981123456`) y no puede repetir a
otro destinatario activo de la misma campaña.
"""
from __future__ import annotations

import datetime as dt
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod, main, models, sso
from app.config import settings
from app.models import Campaign, SurveyTarget, TargetStatus

H = {"Sec-Fetch-Site": "same-origin"}
AHORA = dt.datetime.now(dt.timezone.utc)


@pytest.fixture()
def entorno(monkeypatch):
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
    uno = Campaign(name="Posventa", description="", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48, is_active=True)
    otra = Campaign(name="Entrega", description="", bitrix_entity_type_id=2, trigger_field="y", delay_hours=48, is_active=True)
    s.add_all([uno, otra])
    s.flush()

    def alta(campania, nombre, telefono, estado=TargetStatus.SCHEDULED, **extra):
        t = SurveyTarget(campaign_id=campania.id, contact_name=nombre, phone=telefono, trigger_at=AHORA,
                         scheduled_at=AHORA, status=estado, attempts=1, **extra)
        s.add(t)
        s.flush()
        return t.id

    ids = {
        "ernesto": alta(uno, "Ernesto", "+595976882573"),
        "valeria": alta(uno, "Valeria Martinez", "+595976660421"),
        "cecilia": alta(uno, "Cecilia Meyer", "+595981196515", TargetStatus.COMPLETED),
        "llamando": alta(uno, "Santi", "+595976903185", TargetStatus.CALLING),
        "otra_campania": alta(otra, "Evelyn", "+595981307836"),
        "bitrix": alta(uno, "Pablo", "+595976953263", bitrix_entity_type_id=2, bitrix_entity_id=77),
    }
    s.commit()
    cli = TestClient(main.app, follow_redirects=False)
    cli.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", "admin", "crm"), settings.sso_secret))
    yield cli, S, ids
    main.app.dependency_overrides.pop(dbmod.get_db, None)


def _editar(cli, id_, nombre, telefono):
    return cli.post(f"/targets/{id_}/editar", data={"contact_name": nombre, "phone": telefono}, headers=H)


def _mensaje(r, clave):
    return parse_qs(urlparse(r.headers["location"]).query).get(clave, [""])[0]


def _guardado(S, id_):
    s = S()
    t = s.get(SurveyTarget, id_)
    return t.contact_name, t.phone


def test_cambia_el_nombre_y_el_telefono(entorno):
    cli, S, ids = entorno
    r = _editar(cli, ids["ernesto"], "  Ernesto Doldán ", "0981 123 456")
    assert r.status_code == 303 and r.headers["location"].startswith("/targets")
    assert _guardado(S, ids["ernesto"]) == ("Ernesto Doldán", "+595981123456")
    aviso = _mensaje(r, "aviso")
    assert "Ernesto Doldán" in aviso and "+595981123456" in aviso


def test_solo_el_nombre_no_se_toma_por_duplicado_de_si_mismo(entorno):
    cli, S, ids = entorno
    r = _editar(cli, ids["ernesto"], "Ernesto D.", "+595976882573")
    assert _mensaje(r, "problema") == "" and _guardado(S, ids["ernesto"]) == ("Ernesto D.", "+595976882573")


def test_sin_nombre_queda_sin_nombre(entorno):
    cli, S, ids = entorno
    _editar(cli, ids["ernesto"], "   ", "+595976882573")
    assert _guardado(S, ids["ernesto"]) == (None, "+595976882573")


@pytest.mark.parametrize("telefono", ["", "abc", "12"])
def test_un_telefono_invalido_no_cambia_nada(entorno, telefono):
    cli, S, ids = entorno
    r = _editar(cli, ids["ernesto"], "Otro nombre", telefono)
    assert "teléfono" in _mensaje(r, "problema") and _mensaje(r, "aviso") == ""
    assert _guardado(S, ids["ernesto"]) == ("Ernesto", "+595976882573")  # ni el nombre: todo o nada


def test_no_puede_repetir_el_telefono_de_otro_destinatario_activo_de_la_campania(entorno):
    cli, S, ids = entorno
    r = _editar(cli, ids["ernesto"], "Ernesto", "0976 660 421")  # el de Valeria, normalizado
    assert "Valeria Martinez" in _mensaje(r, "problema")
    assert _guardado(S, ids["ernesto"]) == ("Ernesto", "+595976882573")


def test_si_el_otro_ya_termino_o_es_de_otra_campania_si_se_puede(entorno):
    cli, S, ids = entorno
    assert _mensaje(_editar(cli, ids["ernesto"], "Ernesto", "+595981196515"), "problema") == ""  # Cecilia: completada
    assert _guardado(S, ids["ernesto"])[1] == "+595981196515"
    assert _mensaje(_editar(cli, ids["valeria"], "Valeria", "+595981307836"), "problema") == ""  # Evelyn: otra campaña


def test_en_plena_llamada_no_se_edita(entorno):
    cli, S, ids = entorno
    r = _editar(cli, ids["llamando"], "Santiago", "+595981000000")
    assert "llamada" in _mensaje(r, "problema")
    assert _guardado(S, ids["llamando"]) == ("Santi", "+595976903185")


def test_un_destinatario_inexistente_da_404(entorno):
    cli, _, _ = entorno
    assert _editar(cli, 9999, "x", "+595981000000").status_code == 404


def test_la_cola_ofrece_editar_con_los_datos_actuales(entorno):
    cli, _, ids = entorno
    html = cli.get("/targets").text
    assert f'action="/targets/{ids["ernesto"]}/editar"' in html
    assert 'name="contact_name" value="Ernesto"' in html and 'name="phone" value="+595976882573"' in html


def test_la_cola_muestra_el_problema_de_la_edicion(entorno):
    cli, _, ids = entorno
    r = _editar(cli, ids["ernesto"], "Ernesto", "abc")
    html = cli.get(r.headers["location"]).text
    assert 'class="notice bad"' in html and "teléfono" in html


def test_avisa_que_el_cambio_no_llega_a_bitrix(entorno):
    cli, _, ids = entorno
    html = cli.get("/targets").text
    assert "solo cambia en Callbot" in html
    # y solo en el destinatario que viene de Bitrix
    assert html.count("solo cambia en Callbot") == 1


def test_el_nombre_y_el_telefono_con_comillas_no_rompen_el_formulario(entorno):
    cli, S, ids = entorno
    _editar(cli, ids["ernesto"], 'Ernesto "El Rey" <b>', "+595976882573")
    html = cli.get("/targets").text
    assert 'value="Ernesto &#34;El Rey&#34; &lt;b&gt;"' in html
