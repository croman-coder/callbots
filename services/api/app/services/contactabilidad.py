"""Embudo de contacto y mejor horario para llamar, a partir de los intentos de llamada."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo


def _valor(v):
    return getattr(v, "value", v)


def resumen(intentos: list[tuple[datetime | None, datetime | None, object]], tz: ZoneInfo) -> dict:
    """intentos = [(started_at, answered_at, outcome)].

    Atendida = hubo answered_at. Completa = outcome completed. Las horas son de la zona
    del taller, que es la que sirve para decidir cuándo llamar.
    """
    llamadas = len(intentos)
    atendidas = sum(1 for _, a, _ in intentos if a is not None)
    completas = sum(1 for _, _, o in intentos if _valor(o) == "completed")

    por_hora: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for inicio, atendio, _ in intentos:
        if inicio is None:
            continue
        h = inicio.astimezone(tz).hour
        por_hora[h][0] += 1
        if atendio is not None:
            por_hora[h][1] += 1

    horas = [
        {"hora": h, "llamadas": n, "atendidas": a, "tasa": round(100 * a / n) if n else 0}
        for h, (n, a) in sorted(por_hora.items())
    ]
    # La mejor hora necesita volumen: 1 de 1 no es una tendencia.
    candidatas = [h for h in horas if h["llamadas"] >= 5]
    mejor = max(candidatas, key=lambda h: (h["tasa"], h["llamadas"])) if candidatas else None

    def pct(a, b):
        return round(100 * a / b) if b else 0

    return {
        "llamadas": llamadas,
        "atendidas": atendidas,
        "completas": completas,
        "tasa_contacto": pct(atendidas, llamadas),
        "tasa_completa": pct(completas, atendidas),
        "horas": horas,
        "mejor_hora": mejor,
    }
