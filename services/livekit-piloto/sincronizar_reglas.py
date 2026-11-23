"""Copia las reglas de interpretación de Callbot al piloto.

El piloto lee las respuestas habladas («le doy un nueve», «no me llamen más», «cero, muy malo») con las mismas reglas que Callbot en
producción (`services/api/app/services/scoring.py`) y no con una herramienta de Claude: así no hay una ida y vuelta al LLM por turno y
el puntaje que se anota es el que Callbot anotaría. El archivo vive en la API y el piloto corre en otra imagen, así que se copia con un
solo cambio (el enum `QuestionType` va adentro en vez de importarse de `app.models`).

    python3 sincronizar_reglas.py            # escribe agente/reglas.py
    python3 sincronizar_reglas.py --verificar  # sale con 1 si agente/reglas.py quedó atrasado (lo corre el test)
"""
from __future__ import annotations

import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ORIGEN = AQUI.parent / "api" / "app" / "services" / "scoring.py"
DESTINO = AQUI / "agente" / "reglas.py"

IMPORTAR = "from app.models import QuestionType\n"
ENUM = '''import enum


class QuestionType(str, enum.Enum):
    """Copia de app.models.QuestionType (solo los tipos que usan las reglas)."""

    SCALE_1_10 = "scale_1_10"
    SCALE_1_5 = "scale_1_5"
    YES_NO = "yes_no"
    NUMERIC = "numeric"
    OPEN = "open"
'''
# Un comentario y no una docstring: el original empieza con la suya y `from __future__` tiene que ir antes que cualquier código.
CABECERA = "# GENERADO por sincronizar_reglas.py desde services/api/app/services/scoring.py. No se edita acá: se cambia allá y se vuelve a correr.\n"


def generar() -> str:
    texto = ORIGEN.read_text(encoding="utf-8")
    if IMPORTAR not in texto:
        raise SystemExit(f"{ORIGEN}: ya no importa QuestionType de app.models; hay que ajustar sincronizar_reglas.py")
    texto = texto.replace(IMPORTAR, ENUM)
    # El chequeo manual del final (`python -m app.services.scoring`) vive en la API: acá no hace falta.
    corte = texto.find('\nif __name__ == "__main__":')
    if corte != -1:
        texto = texto[:corte].rstrip() + "\n"
    return CABECERA + texto


if __name__ == "__main__":
    nuevo = generar()
    if "--verificar" in sys.argv:
        actual = DESTINO.read_text(encoding="utf-8") if DESTINO.exists() else ""
        if actual != nuevo:
            print("agente/reglas.py está atrasado: corré python3 sincronizar_reglas.py", file=sys.stderr)
            raise SystemExit(1)
        print("agente/reglas.py al día")
    else:
        DESTINO.write_text(nuevo, encoding="utf-8")
        print(f"escrito {DESTINO}")
