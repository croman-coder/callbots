"""Base de conocimiento

Revision ID: 0013
Revises: 0012

Bases de conocimiento (documentos, páginas web y texto ya pasados a texto) que los agentes usan para responder, y
qué bases usa cada agente.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("descripcion", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "brain_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brain_id", sa.Integer(), sa.ForeignKey("brains.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tipo", sa.String(10), nullable=False),
        sa.Column("titulo", sa.String(200), nullable=False),
        sa.Column("origen", sa.String(500), nullable=True),
        sa.Column("contenido", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_brain_sources_brain_id", "brain_sources", ["brain_id"])
    op.add_column("campaigns", sa.Column("brain_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "brain_ids")
    op.drop_index("ix_brain_sources_brain_id", table_name="brain_sources")
    op.drop_table("brain_sources")
    op.drop_table("brains")
