"""Contexto conversacional por campaña

Lo que el bot puede decir cuando el cliente pregunta algo en vez de responder.
Va por campaña porque el contexto cambia: no es lo mismo una encuesta de taller
que una de entrega de vehículo.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("conversation_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "conversation_prompt")
