"""Que toda plantilla compile: un error de sintaxis Jinja solo aparecería al abrir esa pantalla en producción."""
from pathlib import Path

import pytest

from app.plantillas import templates

NOMBRES = sorted(p.name for p in (Path(__file__).resolve().parent.parent / "app" / "templates").glob("*.html"))


@pytest.mark.parametrize("nombre", NOMBRES)
def test_la_plantilla_compila(nombre):
    templates.env.get_template(nombre)


def test_fechas_en_hora_de_paraguay_y_decimales_con_coma():
    from datetime import datetime, timezone

    from app.plantillas import dec, fecha

    # 13:00 UTC son las 10:00 en Asunción (UTC-3).
    assert fecha(datetime(2026, 10, 20, 13, 0, tzinfo=timezone.utc), "%H:%M") == "10:00"
    assert fecha(None) == "—"
    assert dec(7.46) == "7,5" and dec(1234.5) == "1.234,5" and dec(None) == "—"


def test_estados_en_espanol_para_pantalla_y_excel():
    from app.etiquetas import etiqueta
    from app.models import CallOutcome, TargetStatus
    from app.services.export import RESULTADO_ES

    assert etiqueta(CallOutcome.NO_ANSWER) == "no atendió"
    assert etiqueta(TargetStatus.COMPLETED, "destinatario") == "encuestado"
    assert etiqueta("congestion", "corte") == "red saturada"
    assert etiqueta("algo_nuevo") == "algo nuevo"  # lo desconocido se ve, nunca vacío
    assert RESULTADO_ES["no_answer"] == "No atendió"  # el Excel usa la misma fuente
    assert all(etiqueta(o) != o.value for o in CallOutcome)


def test_duracion_corto_y_dias_legibles():
    from app.plantillas import corto, duracion
    from app.scheduling import texto_dias

    assert duracion(95) == "1:35" and duracion(5) == "0:05" and duracion(None) == "—"
    assert [corto(x) for x in (1.0, 0.667, 1.05)] == ["1", "0,67", "1,05"]
    assert texto_dias({0, 1, 2, 3, 4, 5}) == "lun a sáb"
    assert texto_dias({0, 2, 4}) == "lun, mié, vie"
    assert texto_dias(set(range(7))) == "todos los días"
