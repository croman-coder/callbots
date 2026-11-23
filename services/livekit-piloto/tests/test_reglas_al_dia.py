"""agente/reglas.py es una copia generada de las reglas de Callbot: si la API cambia, el piloto no puede quedarse con las viejas."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sincronizar_reglas  # noqa: E402


def test_reglas_al_dia():
    actual = sincronizar_reglas.DESTINO.read_text(encoding="utf-8")
    assert actual == sincronizar_reglas.generar(), "corré: python3 sincronizar_reglas.py"
