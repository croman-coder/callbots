"""Ficha del agente: contexto de la empresa y pruebas desde el navegador

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("empresa_nombre", sa.String(160), nullable=True))
    op.add_column("campaigns", sa.Column("empresa_descripcion", sa.Text(), nullable=True))
    op.add_column("campaigns", sa.Column("instrucciones_importantes", sa.Text(), nullable=True))
    op.create_table(
        "pruebas_agente",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("usuario", sa.String(160), nullable=False),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("duracion_seconds", sa.Integer(), nullable=True),
        sa.Column("valoracion", sa.String(10), nullable=True),
        sa.Column("transcripcion", sa.JSON(), nullable=True),
    )
    op.create_index("ix_pruebas_agente_campaign_id", "pruebas_agente", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_pruebas_agente_campaign_id", table_name="pruebas_agente")
    op.drop_table("pruebas_agente")
    op.drop_column("campaigns", "instrucciones_importantes")
    op.drop_column("campaigns", "empresa_descripcion")
    op.drop_column("campaigns", "empresa_nombre")
