"""Datos del cliente para el guion

Revision ID: 0014
Revises: 0013

Por destinatario, los datos extra que puede usar el guion (`{{modelo}}`, `{{sucursal}}`…), y por agente, qué campos
de Bitrix se traen como variables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("survey_targets", sa.Column("datos", sa.JSON(), nullable=True))
    op.add_column("campaigns", sa.Column("variables_bitrix", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "variables_bitrix")
    op.drop_column("survey_targets", "datos")
