"""Variables propias del análisis de llamadas

Revision ID: 0009
Revises: 0008

Por agente, una lista de datos extra que Claude saca de cada llamada (`campaigns.analysis_fields`), y por
análisis los valores que sacó (`call_analyses.extras`). Ambos vacíos por defecto: nada cambia hasta que alguien
define una variable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("analysis_fields", sa.JSON(), nullable=True))
    op.add_column("call_analyses", sa.Column("extras", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("call_analyses", "extras")
    op.drop_column("campaigns", "analysis_fields")
