"""Variables propias del análisis: cada agente define datos extra que Claude saca de cada llamada."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace as N
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook

from app import db as dbmod  # noqa: F401
from app.models import Campaign
from app.services import analysis, export, variables, writeback
from tests.test_ficha_agente import H, base, cliente  # noqa: F401  (fixtures)

CAMPOS = [
    {"clave": "motivo_de_la_queja", "nombre": "Motivo de la queja", "tipo": "texto", "descripcion": "por qué se quejó", "opciones": []},
    {"clave": "volveria", "nombre": "Volvería", "tipo": "si_no", "descripcion": "", "opciones": []},
    {"clave": "urgencia", "nombre": "Urgencia", "tipo": "opcion", "descripcion": "", "opciones": ["baja", "media", "alta"]},
    {"clave": "monto", "nombre": "Monto", "tipo": "numero", "descripcion": "", "opciones": []},
]


# ─────────────────────────────────────────────── definición
def test_el_nombre_pasa_a_clave_sin_acentos_ni_simbolos():
    assert variables.clave_de("Motivo de la queja") == "motivo_de_la_queja"
    assert variables.clave_de("¿Volvería a venir?") == "volveria_a_venir"
    assert variables.clave_de("3 cosas") == "v_3_cosas"
    assert variables.clave_de("¡¡!!") == ""


def test_validar_normaliza_y_descarta_las_filas_vacias():
    r = variables.validar([
        {"nombre": "  Motivo   de la queja ", "tipo": "texto", "descripcion": " por qué "},
        {"nombre": "", "tipo": "texto"},  # fila vacía: así se quita una variable
        {"nombre": "Urgencia", "tipo": "opcion", "opciones": "baja, Media,  alta , baja"},
    ])
    assert [v["clave"] for v in r] == ["motivo_de_la_queja", "urgencia"]
    assert r[0]["descripcion"] == "por qué" and r[1]["opciones"] == ["baja", "Media", "alta"]


@pytest.mark.parametrize("filas,motivo", [
    ([{"nombre": "!!!", "tipo": "texto"}], "no sirve como nombre"),
    ([{"nombre": "Resumen", "tipo": "texto"}], "dato fijo"),
    ([{"nombre": "Sentimiento", "tipo": "texto"}], "dato fijo"),
    ([{"nombre": "Costo", "tipo": "texto"}, {"nombre": "costo", "tipo": "numero"}], "dos variables"),
    ([{"nombre": "Algo", "tipo": "raro"}], "Elegí el tipo"),
    ([{"nombre": "Nivel", "tipo": "opcion", "opciones": "sola"}], "entre 2 y 8"),
    ([{"nombre": f"Dato {i}", "tipo": "texto"} for i in range(variables.MAX_VARIABLES + 1)], "Como mucho"),
])
def test_validar_rechaza_lo_que_no_sirve(filas, motivo):
    with pytest.raises(variables.VariablesError, match=motivo):
        variables.validar(filas)


# ─────────────────────────────────────────────── lo que le pedimos a Claude y lo que vuelve
def test_el_pedido_describe_cada_dato_y_tolera_que_no_haya_ninguno():
    texto = variables.instrucciones(CAMPOS)
    assert '"datos"' in texto and "no inventes" in texto
    assert '"motivo_de_la_queja" (texto corto) — por qué se quejó' in texto
    assert '"volveria" (true o false)' in texto and "baja | media | alta" in texto and '"monto" (número)' in texto
    assert variables.instrucciones(None) == "" and variables.instrucciones([]) == ""


def test_lo_que_devuelve_claude_se_lleva_al_tipo_y_lo_dudoso_queda_vacio():
    r = variables.coaccionar(CAMPOS, {
        "motivo_de_la_queja": "  la\n demora  ", "volveria": "Sí", "urgencia": "MEDIA", "monto": "1.500.000 Gs", "extra": "x",
    })
    assert r == {"motivo_de_la_queja": "la demora", "volveria": True, "urgencia": "media", "monto": 1500000}
    r = variables.coaccionar(CAMPOS, {"volveria": "capaz", "urgencia": "urgentísima", "monto": "mucho", "motivo_de_la_queja": None})
    assert r == {"motivo_de_la_queja": None, "volveria": None, "urgencia": None, "monto": None}
    assert variables.coaccionar(CAMPOS, "no es un objeto") == {c["clave"]: None for c in CAMPOS}
    assert variables.coaccionar(None, {"a": 1}) is None


def test_un_texto_gigante_se_acota():
    r = variables.coaccionar(CAMPOS[:1], {"motivo_de_la_queja": "x" * 5000})
    assert len(r["motivo_de_la_queja"]) == variables.MAX_TEXTO


@pytest.mark.parametrize("valor,esperado", [("8,5", 8.5), ("8.5", 8.5), ("2.500", 2500), ("1.500,5", 1500.5), (7, 7), (True, None), ("abc", None)])
def test_numeros_con_formato_de_paraguay(valor, esperado):
    assert variables._numero(valor) == esperado


def test_legible_y_pares_en_el_orden_del_agente():
    assert [variables.legible(v) for v in (None, True, False, 3, "x")] == ["—", "Sí", "No", "3", "x"]
    p = variables.pares(CAMPOS, {"urgencia": "alta", "volveria": False, "vieja": "dato de una variable quitada"})
    assert p == [("Volvería", "No"), ("Urgencia", "alta"), ("Vieja", "dato de una variable quitada")]
    assert variables.pares(CAMPOS, None) == [] and variables.pares(None, {}) == []


# ─────────────────────────────────────────────── el análisis
def test_el_prompt_suma_las_variables_al_final_y_sin_ellas_no_cambia():
    sin = analysis.build_system_prompt("Contexto del agente")
    con = analysis.build_system_prompt("Contexto del agente", CAMPOS)
    assert analysis.build_system_prompt(None) == analysis.SYSTEM_PROMPT
    assert con.startswith(sin) and '"datos"' in con and '"datos"' not in sin


def _llamada(campos):
    q = N(position=1, text="¿Del cero al diez?", counts_for_score=True, qtype=N(value="scale_1_10"))
    return N(id=1, answers=[], target=N(campaign=N(analysis_prompt=None, analysis_fields=campos)), analysis=None)


def test_analizar_guarda_los_datos_propios(monkeypatch):
    visto = {}

    def falso(transcript, timeout=120.0, extra_prompt=None, campos=None):
        visto["campos"] = campos
        return {"sentiment": "negativo", "summary": "x", "topics": ["demora"], "requires_followup": True,
                "followup_reason": "pidió que lo llamen",
                "datos": {"motivo_de_la_queja": "la demora", "volveria": "no", "urgencia": "alta", "monto": 120}}, "claude"

    monkeypatch.setattr(analysis, "query_llm", falso)
    monkeypatch.setattr(analysis, "build_transcript", lambda c: "P1: ¿ok?\nR1: mal")
    db = N(add=lambda a: None, flush=lambda: None)
    resultado = analysis.analyze_call(db, _llamada(CAMPOS))
    assert visto["campos"] == CAMPOS
    assert resultado.extras == {"motivo_de_la_queja": "la demora", "volveria": False, "urgencia": "alta", "monto": 120}


def test_sin_variables_no_hay_extras_y_si_claude_no_respondio_tampoco(monkeypatch):
    monkeypatch.setattr(analysis, "build_transcript", lambda c: "P1: ¿ok?\nR1: bien")
    monkeypatch.setattr(analysis, "query_llm", lambda *a, **k: ({"sentiment": "positivo", "summary": "ok", "topics": []}, "claude"))
    db = N(add=lambda a: None, flush=lambda: None)
    assert analysis.analyze_call(db, _llamada(None)).extras is None
    monkeypatch.setattr(analysis, "query_llm", lambda *a, **k: (None, None))
    assert analysis.analyze_call(db, _llamada(CAMPOS)).extras is None  # no se inventa nada


# ─────────────────────────────────────────────── la ficha del agente
def _guardar(cliente, filas, **extra):
    datos = {"tab": "analisis", "vf_enviado": "1",
             "vf_nombre": [f["nombre"] for f in filas], "vf_tipo": [f.get("tipo", "texto") for f in filas],
             "vf_descripcion": [f.get("descripcion", "") for f in filas], "vf_opciones": [f.get("opciones", "") for f in filas]}
    datos.update(extra)
    return cliente.post("/campaigns/1/guardar", data=datos, headers=H)


def test_la_pestana_muestra_el_editor_de_variables(cliente):
    r = cliente.get("/campaigns/1?tab=analisis")
    assert r.status_code == 200 and "Variables propias" in r.text and "data-var-agregar" in r.text
    assert "Pronto" not in r.text.split("Variables propias")[1].split("Destinos")[0]


def test_se_guardan_y_vuelven_a_mostrarse(cliente, base):
    r = _guardar(cliente, [
        {"nombre": "Motivo de la queja", "tipo": "texto", "descripcion": "por qué se quejó"},
        {"nombre": "Urgencia", "tipo": "opcion", "opciones": "baja, media, alta"},
    ])
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    guardadas = base().get(Campaign, 1).analysis_fields
    assert [(v["clave"], v["tipo"]) for v in guardadas] == [("motivo_de_la_queja", "texto"), ("urgencia", "opcion")]
    pagina = cliente.get("/campaigns/1?tab=analisis").text
    assert 'value="Motivo de la queja"' in pagina and 'value="baja, media, alta"' in pagina


def test_una_fila_con_el_nombre_vacio_quita_la_variable(cliente, base):
    _guardar(cliente, [{"nombre": "Costo", "tipo": "numero"}, {"nombre": "Otra", "tipo": "texto"}])
    _guardar(cliente, [{"nombre": "Costo", "tipo": "numero"}, {"nombre": "", "tipo": "texto"}])
    assert [v["clave"] for v in base().get(Campaign, 1).analysis_fields] == ["costo"]
    _guardar(cliente, [{"nombre": "", "tipo": "texto"}])
    assert base().get(Campaign, 1).analysis_fields is None


def test_otra_pestana_no_toca_las_variables(cliente, base):
    _guardar(cliente, [{"nombre": "Costo", "tipo": "numero"}])
    cliente.post("/campaigns/1/guardar", data={"tab": "analisis", "analysis_prompt": "Algo"}, headers=H)  # sin vf_enviado
    assert [v["clave"] for v in base().get(Campaign, 1).analysis_fields] == ["costo"]


def test_un_error_vuelve_con_el_motivo_y_no_guarda_nada(cliente, base):
    r = _guardar(cliente, [{"nombre": "Resumen", "tipo": "texto"}], analysis_prompt="No debería guardarse")
    assert r.status_code == 303 and "error=" in r.headers["location"] and "dato" in r.headers["location"]
    c = base().get(Campaign, 1)
    assert c.analysis_fields is None and c.analysis_prompt is None


# ─────────────────────────────────────────────── donde se ven los datos
def _llamada_completa(extras):
    campana = N(name="Posventa", analysis_fields=CAMPOS)
    target = N(contact_name="Juan", phone="+595981123456", campaign=campana, bitrix_entity_type_id=1, bitrix_entity_id=9,
               trigger_at=datetime(2026, 10, 19, tzinfo=timezone.utc))
    analisis = N(satisfaction_score=6.0, sentiment="negativo", requires_followup=True, followup_reason="pidió que lo llamen",
                 followup_done_at=None, followup_done_by=None, summary="Se quejó", topics=["demora"], extras=extras)
    q = N(id=1, campaign_id=1, position=1, text="¿Del cero al diez?", qtype=N(value="scale_1_10"), counts_for_score=True)
    return N(id=5, started_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), outcome=N(value="completed"), attempt_number=1,
             duration_seconds=62, questions_answered=1, questions_asked=1, target=target, analysis=analisis,
             answers=[N(question_id=1, question=q, transcript="un seis", value_numeric=6.0, asr_confidence=0.9, retries_used=0)])


def test_el_excel_trae_una_columna_por_dato_con_el_valor_o_vacio():
    a = _llamada_completa({"motivo_de_la_queja": "la demora", "volveria": False, "urgencia": None, "monto": 1500})
    b = _llamada_completa(None)
    contenido, _ = export.construir_excel([a, b], ZoneInfo("America/Asuncion"))
    hoja = load_workbook(BytesIO(contenido))["Llamadas"]
    cab = [c.value for c in hoja[1]]
    for nombre in ("Motivo de la queja", "Volvería", "Urgencia", "Monto"):
        assert nombre in cab
    fila = {cab[i]: c.value for i, c in enumerate(hoja[2])}
    assert (fila["Motivo de la queja"], fila["Volvería"], fila["Urgencia"], fila["Monto"]) == ("la demora", "No", None, 1500)
    sin = {cab[i]: c.value for i, c in enumerate(hoja[3])}
    assert sin["Motivo de la queja"] is None and sin["Monto"] is None


def test_bitrix_recibe_solo_los_datos_que_se_pudieron_saber():
    comentario = writeback.build_comment(_llamada_completa({"motivo_de_la_queja": "la demora", "volveria": False, "urgencia": None, "monto": None}))
    assert "[B]Datos:[/B]" in comentario and "Motivo de la queja: la demora" in comentario and "Volvería: No" in comentario
    assert "Urgencia" not in comentario and "Monto" not in comentario  # sin dato: no se escribe
    assert "[B]Datos:[/B]" not in writeback.build_comment(_llamada_completa(None))
