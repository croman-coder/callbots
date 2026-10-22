"""Locutor a distancia: un enlace para que una persona grabe su permiso y varias tomas desde su celular

Revision ID: 0022
Revises: 0021

- grabaciones_voz: el enlace (de un solo uso, vence), quién tiene que grabar, las tomas recibidas y qué se hizo con
  ellas (aprobada → voz clonada en VoiceStudio, o rechazada). Los audios quedan en RECORDINGS_DIR/locutores/<id>/.
Ver docs/voz-de-locutor.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grabaciones_voz",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("persona", sa.String(80), nullable=False),
        sa.Column("genero", sa.String(10), nullable=False),
        sa.Column("nota", sa.String(300), nullable=True),
        sa.Column("creada_por", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expira_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False, server_default="esperando"),
        sa.Column("recibida_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tomas", sa.JSON(), nullable=True),
        sa.Column("consentimiento_texto", sa.Text(), nullable=True),
        sa.Column("perfil_id", sa.String(80), nullable=True),
        sa.Column("revisada_por", sa.String(160), nullable=True),
        sa.Column("revisada_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("motivo", sa.String(300), nullable=True),
    )
    op.create_index("ix_grabaciones_voz_token", "grabaciones_voz", ["token"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_grabaciones_voz_token", table_name="grabaciones_voz")
    op.drop_table("grabaciones_voz")
