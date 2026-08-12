#!/usr/bin/env python3
"""Crea una campaña de ejemplo con preguntas típicas de posventa de taller.

Sirve para tener algo funcionando en minutos y después ajustar desde el panel.
Se crea PAUSADA a propósito: revisá el guion y el campo disparador antes de que
empiece a llamar a clientes reales.

Uso:
    docker compose exec api python scripts/seed_campaign.py
    docker compose exec api python scripts/seed_campaign.py --trigger-field ufCrm5_1639669411830
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import Campaign, Question, QuestionType

# (texto, tipo, cuenta_para_puntaje, segundos_max)
#
# Todas las escalas van de 0 a 10 y solo 9 o 10 cuentan como satisfactorio, así
# que el guion dice el rango en voz alta: si no, el cliente responde en la escala
# que se le ocurra y la respuesta queda inservible.
PREGUNTAS = [
    (
        "Del cero al diez, ¿qué tan satisfecho quedó con el trabajo realizado en su vehículo?",
        QuestionType.SCALE_1_10, True, 15,
    ),
    (
        "Del cero al diez, ¿cómo calificaría la atención del personal que lo recibió?",
        QuestionType.SCALE_1_10, True, 15,
    ),
    (
        "Del cero al diez, ¿qué tan conforme quedó con el plazo de entrega?",
        QuestionType.SCALE_1_10, True, 15,
    ),
    (
        "Del cero al diez, ¿qué tan claro le resultó el presupuesto antes del trabajo?",
        QuestionType.SCALE_1_10, True, 15,
    ),
    (
        "Del cero al diez, ¿qué tan probable es que nos recomiende a un conocido?",
        QuestionType.SCALE_1_10, True, 15,
    ),
    (
        "Para terminar, ¿hay algo que podríamos mejorar?",
        QuestionType.OPEN, False, 35,
    ),
]

INTRO = (
    "Hola {nombre}, buenos días. Le hablamos del servicio de posventa del taller. "
    "Estamos haciendo una encuesta muy breve sobre su última visita, son seis "
    "preguntas cortas y se responden con un número del cero al diez. "
    "¿Nos regala un minuto?"
)

OUTRO = (
    "Muchas gracias por su tiempo, sus comentarios nos ayudan a mejorar. "
    "Que tenga muy buen día."
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--name",
        default="Satisfacción post-taller",
        help="Nombre de la campaña.",
    )
    parser.add_argument(
        "--entity-type-id",
        type=int,
        default=settings.bitrix_entity_type_id,
        help="entityTypeId de Bitrix. Descubrilo con bitrix_discover.py.",
    )
    parser.add_argument(
        "--trigger-field",
        default=settings.bitrix_field_workshop_entry,
        help="Campo de fecha que marca el ingreso al taller (T0).",
    )
    parser.add_argument(
        "--delay-hours", type=int, default=settings.survey_delay_hours
    )
    args = parser.parse_args()

    with session_scope() as db:
        existing = db.scalar(select(Campaign).where(Campaign.name == args.name))
        if existing:
            sys.exit(
                f"Ya existe una campaña llamada {args.name!r} (id={existing.id}).\n"
                f"Editala en el panel o usá --name con otro nombre."
            )

        campaign = Campaign(
            name=args.name,
            description="Encuesta automática 48hs después del ingreso al taller",
            bitrix_entity_type_id=args.entity_type_id,
            trigger_field=args.trigger_field,
            delay_hours=args.delay_hours,
            call_window_start=settings.call_window_start.strftime("%H:%M"),
            call_window_end=settings.call_window_end.strftime("%H:%M"),
            call_window_days=settings.call_window_days,
            max_attempts=settings.max_call_attempts,
            retry_interval_minutes=settings.retry_interval_minutes,
            intro_script=INTRO,
            outro_script=OUTRO,
            is_active=False,
        )
        db.add(campaign)
        db.flush()

        for position, (text, qtype, counts, seconds) in enumerate(PREGUNTAS, start=1):
            db.add(
                Question(
                    campaign_id=campaign.id,
                    position=position,
                    text=text,
                    qtype=qtype,
                    counts_for_score=counts,
                    max_answer_seconds=seconds,
                    max_retries=1,
                )
            )

        db.flush()
        campaign_id = campaign.id

    print(f"Campaña creada: id={campaign_id} con {len(PREGUNTAS)} preguntas")
    print(f"  entityTypeId  : {args.entity_type_id}")
    print(f"  campo T0      : {args.trigger_field}")
    print(f"  demora        : {args.delay_hours}h")
    print()
    print("Está PAUSADA. Antes de activarla:")
    print(f"  1. Revisá el guion en http://localhost:8000/campaigns/{campaign_id}")
    print("  2. Probala llamando al 9000 desde el softphone")
    print("  3. Recién ahí marcá 'Campaña activa'")


if __name__ == "__main__":
    main()
