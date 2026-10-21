"""Webhooks de resultados

Revision ID: 0015
Revises: 0014

Por agente, las URLs a las que se manda cada llamada terminada y el secreto con que se firma; y el registro de
envíos.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("webhooks", sa.JSON(), nullable=True))
    op.add_column("campaigns", sa.Column("webhook_secreto", sa.String(64), nullable=True))
    op.create_table(
        "webhook_envios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("estado", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(300), nullable=True),
        sa.Column("duracion_ms", sa.Integer(), nullable=True),
        sa.Column("prueba", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_envios_campaign_id", "webhook_envios", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_envios_campaign_id", table_name="webhook_envios")
    op.drop_table("webhook_envios")
    op.drop_column("campaigns", "webhook_secreto")
    op.drop_column("campaigns", "webhooks")
