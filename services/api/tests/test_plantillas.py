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
