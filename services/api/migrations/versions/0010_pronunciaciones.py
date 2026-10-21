"""Diccionario de pronunciación

Revision ID: 0010
Revises: 0009

Cómo tiene que decir el bot las palabras que un sintetizador en español lee mal (marcas, siglas). Se siembran
solo las que tienen una única forma de decirse (siglas deletreadas y marcas con pronunciación conocida): el resto
lo carga el equipo en Voces → Pronunciación, escuchando el resultado.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEMILLA = [
    ("GWM", "Ge Doble Ve Eme"),
    ("JMEV", "Jota Eme E Ve"),
    ("Renault", "Renó"),
    ("Zeekr", "Zíker"),
    ("Leapmotor", "Lip motor"),
]


def upgrade() -> None:
    tabla = op.create_table(
        "pronunciations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("palabra", sa.String(60), nullable=False),
        sa.Column("dicho", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ux_pronunciations_palabra", "pronunciations", [sa.text("lower(palabra)")], unique=True)
    op.bulk_insert(tabla, [{"palabra": p, "dicho": d} for p, d in SEMILLA])


def downgrade() -> None:
    op.drop_index("ux_pronunciations_palabra", table_name="pronunciations")
    op.drop_table("pronunciations")
