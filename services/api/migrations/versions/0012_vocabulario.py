"""Vocabulario del agente para la transcripción

Revision ID: 0012
Revises: 0011

Por agente, las palabras que Whisper tiene que reconocer en lo que dice el cliente (marcas, modelos, sucursales).
Vacío por defecto: igual se usan las palabras del diccionario de pronunciación.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("vocabulario", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "vocabulario")
