"""Que toda plantilla compile: un error de sintaxis Jinja solo aparecería al abrir esa pantalla en producción."""
from pathlib import Path

import pytest

from app.plantillas import templates

NOMBRES = sorted(p.name for p in (Path(__file__).resolve().parent.parent / "app" / "templates").glob("*.html"))


@pytest.mark.parametrize("nombre", NOMBRES)
def test_la_plantilla_compila(nombre):
    templates.env.get_template(nombre)
