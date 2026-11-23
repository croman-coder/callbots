from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.texto import hay_bucle  # noqa: E402


def test_el_bucle_de_la_prueba_del_23_11_es_un_bucle():
    texto = "Sí, tengo, dale, claro, bueno, ahora no puedo" + ", no puedo" * 30
    assert hay_bucle(texto)


def test_respuestas_de_verdad_no_son_bucles():
    for t in ["Le doy un nueve.", "No, no, no, no me gustó nada", "Sí, doble, doble, ¿cómo no?", "Cero, cero, cero, uno, dos, tres",
              "Claro que no me gustó para nada, fue un desastre y un quilombo, no me gusta nada.", "", "Ocho, ocho"]:
        assert not hay_bucle(t), t


def test_una_palabra_repetida_cinco_veces_si():
    assert hay_bucle("gracias gracias gracias gracias gracias")
