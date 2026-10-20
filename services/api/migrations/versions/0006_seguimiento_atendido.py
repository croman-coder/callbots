"""Seguimiento atendido: quién llamó de vuelta al cliente y cuándo

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("call_analyses", sa.Column("followup_done_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_analyses", sa.Column("followup_done_by", sa.String(160), nullable=True))
    op.add_column("call_analyses", sa.Column("followup_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("call_analyses", "followup_note")
    op.drop_column("call_analyses", "followup_done_by")
    op.drop_column("call_analyses", "followup_done_at")
