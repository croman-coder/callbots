"""Datos del cliente en el guion: {{modelo}}, {{sucursal}}… desde el Excel o desde Bitrix."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as N

from app import datos_cliente
from app.bitrix import sync
from app.models import Campaign, SurveyTarget
from app.routers import internal
from app.services import conversation, importar
from tests.test_buzon_y_transferencia import INTERNO, Sesion, _agente, api  # noqa: F401
from tests.test_ficha_agente import H, base, cliente  # noqa: F401
from tests.test_mejoras_dapta import _xlsx


# ─────────────────────────────────────────────── plantilla
def test_render_con_dato_sin_dato_y_con_texto_alternativo():
    d = {"modelo": "Kardian", "sucursal": "Mariano Roque Alonso"}
    assert datos_cliente.render("¿Cómo anda su {{modelo}}?", d) == "¿Cómo anda su Kardian?"
    assert datos_cliente.render("¿Cómo anda su {{ asesor | vehículo }}?", d) == "¿Cómo anda su vehículo?"
    assert datos_cliente.render("Lo atendimos en {{sucursal}}, {{asesor}}.", d) == "Lo atendimos en Mariano Roque Alonso."
    assert datos_cliente.render("Hola {nombre}, ¿cómo está?", d) == "Hola {nombre}, ¿cómo está?"  # la de una llave es del voice-agent
    assert datos_cliente.render(None, d) == ""


def test_valores_fijos_y_extra():
    t = N(contact_name="Juan Pérez", trigger_at=dt.datetime(2026, 10, 20, 15, 0, tzinfo=dt.timezone.utc),
          invoice_date=None, delivery_date=None, bitrix_title="Service 10.000 km", datos={"modelo": "Kardian", "nombre": "pisado"})
    v = datos_cliente.valores(t, N(empresa_nombre="Santa Rosa"))
    assert v["nombre"] == "Juan" and v["nombre_completo"] == "Juan Pérez" and v["fecha_ingreso"] == "20/10"
    assert v["modelo"] == "Kardian" and v["titulo"] == "Service 10.000 km" and v["empresa"] == "Santa Rosa"
    assert "fecha_factura" not in v  # sin dato no aparece


def test_un_dato_no_puede_meter_llaves_en_el_guion():
    v = datos_cliente.valores(N(contact_name=None, datos={"modelo": "{{nombre}} malo"}), None)
    assert "{" not in v["modelo"]


def test_mapa_bitrix():
    m = datos_cliente.mapa_bitrix("modelo = ufCrm5_170\nSucursal=ufCrm5_171\nmal formado\nnombre = ufCrm5_1\nx = 1nope")
    assert m == {"modelo": "ufCrm5_170", "sucursal": "ufCrm5_171"}  # «nombre» es fija: no se pisa
    assert datos_cliente.mapa_a_texto(m) == "modelo = ufCrm5_170\nsucursal = ufCrm5_171"


def test_para_claude():
    b = datos_cliente.para_claude({"modelo": "Kardian", "empresa": "Santa Rosa"})
    assert "<cliente>" in b and "modelo: Kardian" in b and "empresa" not in b
    assert datos_cliente.para_claude({}) == ""


# ─────────────────────────────────────────────── de dónde salen
def test_el_excel_trae_las_columnas_extra_como_datos():
    archivo = _xlsx([["Cliente", "Celular", "Modelo", "Sucursal", ""], ["Juan", "0981 123 456", "Kardian", "MRA", "x"]])
    filas = importar.leer_filas_con_datos(archivo, "lista.xlsx")
    assert filas == [("0981 123 456", "Juan", {"modelo": "Kardian", "sucursal": "MRA"})]
    assert importar.leer_filas(archivo, "lista.xlsx") == [("0981 123 456", "Juan")]  # la de siempre no cambia


def test_bitrix_trae_los_campos_elegidos():
    item = {"ufCrm5_1": "Kardian", "ufCrm5_2": ["Lavado", "Alineación"], "ufCrm5_3": "", "ufCrm5_4": True,
            "ufCrm5_5": "2026-10-20T10:00:00+03:00"}
    datos = sync._datos_de_item(item, {"modelo": "ufCrm5_1", "trabajos": "ufCrm5_2", "vacio": "ufCrm5_3",
                                       "garantia": "ufCrm5_4", "turno": "ufCrm5_5"})
    assert datos == {"modelo": "Kardian", "trabajos": "Lavado, Alineación", "garantia": "sí", "turno": "20/10"}
    assert sync._datos_de_item({}, {"modelo": "ufCrm5_1"}) is None


# ─────────────────────────────────────────────── en la llamada
def _con_datos(S, **campos):
    _, sesion = _agente(S, **campos)
    s = S()
    t = s.query(SurveyTarget).order_by(SurveyTarget.id.desc()).first()
    t.datos = {"modelo": "Kardian"}
    s.commit()
    return sesion


def test_el_guion_de_la_llamada_lleva_los_datos(Sesion, api):
    sesion = _con_datos(Sesion, intro_script="Hola {nombre}, ¿cómo anda su {{modelo|vehículo}}?")
    g = api.get(f"/internal/sessions/{sesion}/script").json()
    assert g["intro_script"] == "Hola {nombre}, ¿cómo anda su Kardian?" and g["contact_name"] == "Marta"


def test_la_prueba_usa_los_datos_del_ultimo_destinatario_sin_su_nombre(Sesion, api):
    sesion = _con_datos(Sesion, intro_script="Hola {{nombre|}}, ¿cómo anda su {{modelo|vehículo}}?")
    cid = Sesion().query(Campaign).one().id
    g = api.get(f"/internal/sessions/00000000-0000-0000-0000-{cid:012d}/script").json()
    assert g["intro_script"] == "Hola, ¿cómo anda su Kardian?"


def test_el_precalentado_incluye_las_frases_de_cada_cliente(Sesion, api):
    _con_datos(Sesion, intro_script="¿Cómo anda su {{modelo|vehículo}}?")
    s = Sesion()
    t = s.query(SurveyTarget).one()
    t.status = internal.TargetStatus.PENDING
    s.commit()
    textos = api.get("/internal/prompts").json()["texts"]
    assert "¿Cómo anda su Kardian?" in textos and "¿Cómo anda su vehículo?" in textos


def test_claude_recibe_los_datos_del_cliente(Sesion, api, monkeypatch):
    sesion = _con_datos(Sesion)
    visto = {}

    async def falso(pregunta, dijo, intento=0, extra_prompt=None, conocimiento=""):
        visto["c"] = conocimiento
        return "Es por su Kardian. ¿Del cero al diez?"

    monkeypatch.setattr(conversation, "reply", falso)
    api.post(f"/internal/sessions/{sesion}/reply", json={"question_text": "¿Del cero al diez?", "transcript": "¿de qué auto?"})
    assert "modelo: Kardian" in visto["c"] and "Marta" in visto["c"]


def test_la_prueba_de_un_agente_responde_con_ese_agente_y_no_con_el_primero(Sesion, api, monkeypatch):
    _agente(Sesion, conversation_prompt="Sos el agente A")
    cid_b, _ = _agente(Sesion, conversation_prompt="Sos el agente B")
    visto = {}

    async def falso(pregunta, dijo, intento=0, extra_prompt=None, conocimiento=""):
        visto["p"] = extra_prompt
        return "ok ¿Del cero al diez?"

    monkeypatch.setattr(conversation, "reply", falso)
    api.post(f"/internal/sessions/00000000-0000-0000-0000-{cid_b:012d}/reply", json={"question_text": "¿Del cero al diez?", "transcript": "¿qué?"})
    assert visto["p"] == "Sos el agente B"


# ─────────────────────────────────────────────── en la ficha
def test_la_ficha_muestra_las_variables_y_guarda_los_campos_de_bitrix(cliente, base):
    s = base()
    s.add(SurveyTarget(campaign_id=1, phone="+595981000001", trigger_at=dt.datetime.now(dt.timezone.utc),
                       scheduled_at=dt.datetime.now(dt.timezone.utc), datos={"sucursal": "MRA"}))
    s.commit()
    cliente.post("/campaigns/1/guardar", data={"tab": "configuracion", "variables_bitrix": "modelo = ufCrm5_170"}, headers=H)
    assert base().get(Campaign, 1).variables_bitrix == {"modelo": "ufCrm5_170"}
    pagina = cliente.get("/campaigns/1?tab=configuracion").text
    for clave in ("nombre", "fecha_ingreso", "modelo", "sucursal"):
        assert f'data-variable="{clave}"' in pagina
