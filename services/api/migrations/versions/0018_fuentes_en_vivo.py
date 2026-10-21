"""Datos en vivo para los agentes de recepción (Cotizador de repuestos)

Revision ID: 0018
Revises: 0017

- campaigns.fuentes: qué sistemas consulta el agente en cada turno y qué puede decir de ellos,
  por ejemplo {"repuestos": true, "precios": false}.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("fuentes", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "fuentes")
