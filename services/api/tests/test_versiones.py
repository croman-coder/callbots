"""Historial de versiones del agente: qué cambió, quién y cuándo, y volver atrás."""
from __future__ import annotations

from app import versiones
from app.models import AgentVersion, Campaign
from tests.test_ficha_agente import H, base, cliente  # noqa: F401  (fixtures)


def _versiones(base):
    return base().query(AgentVersion).order_by(AgentVersion.id).all()


def test_guardar_sin_cambios_no_anota_nada(cliente, base):
    c = base().get(Campaign, 1)
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "name": c.name}, headers=H)
    assert _versiones(base) == []


def test_el_primer_cambio_anota_tambien_como_estaba_antes(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": "Sos Sofía."}, headers=H)
    vs = _versiones(base)
    assert len(vs) == 2
    assert vs[0].snapshot["conversation_prompt"] is None and "antes del primer cambio" in vs[0].resumen
    assert vs[1].snapshot["conversation_prompt"] == "Sos Sofía."
    assert vs[1].resumen == "Cambió: Instrucciones del agente" and vs[1].created_by == "ana@santarosa.com.py"
    # el segundo cambio ya no repite el punto de partida
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": "Sos Sofía, de Santa Rosa."}, headers=H)
    assert len(_versiones(base)) == 3


def test_la_pestana_muestra_que_cambio(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": "Sos Sofía."}, headers=H)
    r = cliente.get("/campaigns/1?tab=versiones")
    assert r.status_code == 200 and "Historial de versiones" in r.text
    assert "Instrucciones del agente" in r.text and "Sos Sofía." in r.text and "actual" in r.text
    assert "Volver a esta versión" in r.text  # a la del punto de partida se puede volver


def test_volver_a_una_version_restaura_y_queda_anotado(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": "Versión A"}, headers=H)
    cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": "Versión B"}, headers=H)
    a = [v for v in _versiones(base) if v.snapshot.get("conversation_prompt") == "Versión A"][0]
    r = cliente.post(f"/campaigns/1/versiones/{a.id}/restaurar", headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert base().get(Campaign, 1).conversation_prompt == "Versión A"
    ultima = _versiones(base)[-1]
    assert ultima.resumen.startswith("Volvió a la versión del") and ultima.snapshot["conversation_prompt"] == "Versión A"
    # restaurar la misma otra vez no cambia nada ni anota otra versión
    n = len(_versiones(base))
    r = cliente.post(f"/campaigns/1/versiones/{a.id}/restaurar", headers=H)
    assert "no+se+cambi" in r.headers["location"].lower() or "no%20se%20cambi" in r.headers["location"].lower()
    assert len(_versiones(base)) == n


def test_no_se_puede_restaurar_la_version_de_otro_agente(cliente, base):
    s = base()
    otra = Campaign(name="Otra", description="", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48, is_active=False)
    s.add(otra)
    s.commit()
    s.add(AgentVersion(campaign_id=otra.id, created_by=None, resumen="x", snapshot={"conversation_prompt": "Ajena"}))
    s.commit()
    vid = s.query(AgentVersion).filter_by(campaign_id=otra.id).one().id
    r = cliente.post(f"/campaigns/1/versiones/{vid}/restaurar", headers=H)
    assert "error=" in r.headers["location"] and base().get(Campaign, 1).conversation_prompt != "Ajena"


def test_el_historial_tiene_tope(cliente, base, monkeypatch):
    monkeypatch.setattr(versiones, "MAX_VERSIONES", 3)
    for i in range(6):
        cliente.post("/campaigns/1/guardar", data={"tab": "contexto", "conversation_prompt": f"v{i}"}, headers=H)
    vs = _versiones(base)
    assert len(vs) == 3 and vs[-1].snapshot["conversation_prompt"] == "v5"


def test_un_error_de_validacion_no_anota_version(cliente, base):
    cliente.post("/campaigns/1/guardar", data={"tab": "analisis", "vf_enviado": "1", "vf_nombre": "Resumen", "vf_tipo": "texto",
                                               "vf_descripcion": "", "vf_opciones": "", "analysis_prompt": "x"}, headers=H)
    assert _versiones(base) == []


def test_diferencias_legibles_y_acotadas():
    d = versiones.diferencias({"name": "A", "analysis_fields": None, "conversation_prompt": "x" * 2000},
                              {"name": "B", "analysis_fields": [{"nombre": "Motivo"}], "conversation_prompt": "x" * 2000})
    assert ("Nombre", "A", "B") in d and ("Análisis: variables propias", "(vacío)", "Motivo") in d
    assert all(len(a) <= versiones.MAX_TEXTO_DIFERENCIA + 1 and len(b) <= versiones.MAX_TEXTO_DIFERENCIA + 1 for _, a, b in d)
    assert versiones.diferencias(None, {"name": "A"}) == []


def test_la_hora_del_resumen_es_la_de_paraguay():
    from datetime import datetime, timezone

    assert versiones._hora_local(datetime(2026, 10, 21, 14, 9, tzinfo=timezone.utc)) == "21/10/2026 11:09"
    assert versiones._hora_local(None) == "antes"
