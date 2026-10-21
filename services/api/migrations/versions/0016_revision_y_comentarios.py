"""Revisión a mano del resultado, corrección de respuestas y comentarios

Revision ID: 0016
Revises: 0015

- call_attempts: el resultado revisado a mano (exitosa / no exitosa), con motivo, quién y cuándo.
- answers: el valor que había antes de corregirlo a mano, quién y cuándo.
- call_comments: comentarios del equipo sobre cada llamada.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("call_attempts", sa.Column("revision", sa.String(12), nullable=True))
    op.add_column("call_attempts", sa.Column("revision_motivo", sa.Text(), nullable=True))
    op.add_column("call_attempts", sa.Column("revision_por", sa.String(160), nullable=True))
    op.add_column("call_attempts", sa.Column("revision_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("answers", sa.Column("valor_original", sa.Float(), nullable=True))
    op.add_column("answers", sa.Column("corregido_por", sa.String(160), nullable=True))
    op.add_column("answers", sa.Column("corregido_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "call_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("call_id", sa.Integer(), sa.ForeignKey("call_attempts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("autor", sa.String(160), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_call_comments_call_id", "call_comments", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_call_comments_call_id", table_name="call_comments")
    op.drop_table("call_comments")
    for col in ("corregido_at", "corregido_por", "valor_original"):
        op.drop_column("answers", col)
    for col in ("revision_at", "revision_por", "revision_motivo", "revision"):
        op.drop_column("call_attempts", col)
