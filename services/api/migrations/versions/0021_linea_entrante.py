"""Línea entrante: qué agente de recepción atiende las llamadas que entran por la troncal

Revision ID: 0021
Revises: 0020

- campaigns.entrante: el agente atiende llamadas entrantes.
- campaigns.numeros_entrantes: a qué números (uno por línea); vacío = cualquier número de la troncal.
Ver docs/linea-entrante.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("entrante", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("campaigns", sa.Column("numeros_entrantes", sa.Text(), nullable=True))
    op.create_index("ix_casos_session_uuid", "casos", ["session_uuid"])


def downgrade() -> None:
    op.drop_index("ix_casos_session_uuid", table_name="casos")
    op.drop_column("campaigns", "numeros_entrantes")
    op.drop_column("campaigns", "entrante")
