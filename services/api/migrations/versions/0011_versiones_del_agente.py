"""Historial de versiones del agente

Revision ID: 0011
Revises: 0010

Una fila por cada vez que se guardan cambios en la configuración de un agente: qué cambió, quién y la
configuración completa de ese momento (JSON), para poder volver atrás.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("resumen", sa.String(400), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
    )
    op.create_index("ix_agent_versions_campaign_id", "agent_versions", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_versions_campaign_id", table_name="agent_versions")
    op.drop_table("agent_versions")
