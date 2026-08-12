"""Cálculo de cuándo se puede llamar.

Nadie quiere una encuesta a las 3 de la mañana ni un domingo: toda fecha
calculada se corre al próximo horario hábil configurado en la campaña.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_hhmm(value: str, fallback: time) -> time:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return fallback


def parse_days(value: str) -> set[int]:
    """'0,1,2,3,4,5' -> {0,1,2,3,4,5}. Lunes = 0."""
    days = {int(d.strip()) for d in (value or "").split(",") if d.strip().isdigit()}
    return days or {0, 1, 2, 3, 4, 5}


def next_call_slot(
    moment: datetime,
    window_start: time,
    window_end: time,
    allowed_days: set[int],
    tz: ZoneInfo,
    max_lookahead_days: int = 14,
) -> datetime:
    """Devuelve (en UTC) el primer instante llamable a partir de `moment`.

    - Si `moment` cae dentro de la ventana de un día hábil, se devuelve igual.
    - Si cae antes de la apertura, se corre a la apertura de ese día.
    - Si cae después del cierre o en día no hábil, salta al próximo día hábil.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)

    local = moment.astimezone(tz)

    for offset in range(max_lookahead_days + 1):
        day = local + timedelta(days=offset)

        if day.weekday() not in allowed_days:
            continue

        opens = day.replace(
            hour=window_start.hour, minute=window_start.minute, second=0, microsecond=0
        )
        closes = day.replace(
            hour=window_end.hour, minute=window_end.minute, second=0, microsecond=0
        )

        if offset > 0:
            # Días siguientes: siempre arrancamos en la apertura
            return opens.astimezone(timezone.utc)

        if local < opens:
            return opens.astimezone(timezone.utc)
        if opens <= local <= closes:
            return local.astimezone(timezone.utc)
        # Pasó el cierre: seguimos buscando en el día siguiente

    # Ningún día hábil en dos semanas (configuración rota): devolvemos el original
    return moment.astimezone(timezone.utc)


def is_within_window(
    moment: datetime,
    window_start: time,
    window_end: time,
    allowed_days: set[int],
    tz: ZoneInfo,
) -> bool:
    """¿Se puede llamar justo ahora?"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(tz)
    if local.weekday() not in allowed_days:
        return False
    return window_start <= local.time() <= window_end
