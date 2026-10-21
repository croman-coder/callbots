"""Recepción: agentes que atienden cuando el cliente llama, y los casos que derivan a cada área

Revision ID: 0017
Revises: 0016

- campaigns.modo: «encuesta» (el agente llama y pregunta) o «recepcion» (atiende y deriva).
- campaigns.areas: a qué áreas puede derivar, con su responsable y el plazo para contestar.
- campaigns.limites: lo que el agente no puede decir ni prometer.
- casos: cada consulta o reclamo que entra, con la conversación, el área, el plazo y su seguimiento.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("modo", sa.String(12), nullable=False, server_default="encuesta"))
    op.add_column("campaigns", sa.Column("areas", sa.JSON(), nullable=True))
    op.add_column("campaigns", sa.Column("limites", sa.Text(), nullable=True))
    op.create_table(
        "casos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origen", sa.String(12), nullable=False),
        sa.Column("session_uuid", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("telefono", sa.String(30), nullable=True),
        sa.Column("nombre", sa.String(160), nullable=True),
        sa.Column("motivo", sa.String(12), nullable=True),
        sa.Column("urgente", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resumen", sa.Text(), nullable=True),
        sa.Column("datos", sa.JSON(), nullable=True),
        sa.Column("conversacion", sa.JSON(), nullable=True),
        sa.Column("area", sa.String(40), nullable=True),
        sa.Column("area_nombre", sa.String(80), nullable=True),
        sa.Column("responsable", sa.String(120), nullable=True),
        sa.Column("correo_responsable", sa.String(160), nullable=True),
        sa.Column("plazo_horas", sa.Integer(), nullable=True),
        sa.Column("estado", sa.String(12), nullable=False, server_default="en_llamada"),
        sa.Column("vence_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("derivado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respondido_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cerrado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respuesta", sa.Text(), nullable=True),
        sa.Column("eventos", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_casos_campaign_id", "casos", ["campaign_id"])
    op.create_index("ix_casos_estado", "casos", ["estado"])
    op.create_index("ix_casos_created_at", "casos", ["created_at"])


def downgrade() -> None:
    for indice in ("ix_casos_created_at", "ix_casos_estado", "ix_casos_campaign_id"):
        op.drop_index(indice, table_name="casos")
    op.drop_table("casos")
    for col in ("limites", "areas", "modo"):
        op.drop_column("campaigns", col)
