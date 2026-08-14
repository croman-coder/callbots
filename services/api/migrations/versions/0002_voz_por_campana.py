"""Parámetros de voz e instrucciones de análisis por campaña

Velocidad, tono, expresividad y volumen del bot. Van en la campaña y no en el
entorno para poder ajustarlos desde el panel sin redesplegar: el voice-agent
los recibe en el guion de cada llamada.

Los defaults son los de Piper, así que una campaña existente suena igual que
antes de esta migración.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNAS = (
    ("voice_speed", 1.0),
    ("voice_pitch", 1.0),
    ("voice_expressiveness", 0.667),
    ("voice_volume", 1.0),
)


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("analysis_prompt", sa.Text(), nullable=True))

    for nombre, default in COLUMNAS:
        op.add_column(
            "campaigns",
            sa.Column(
                nombre,
                sa.Float(),
                nullable=False,
                # server_default rellena las filas que ya existen; se saca
                # después para que el default lo ponga la aplicación.
                server_default=sa.text(str(default)),
            ),
        )
        op.alter_column("campaigns", nombre, server_default=None)


def downgrade() -> None:
    for nombre, _ in reversed(COLUMNAS):
        op.drop_column("campaigns", nombre)
    op.drop_column("campaigns", "analysis_prompt")
