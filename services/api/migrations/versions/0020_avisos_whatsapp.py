"""Avisos por WhatsApp: qué se avisa con qué plantilla, y cada envío con su estado

Revision ID: 0020
Revises: 0019

- avisos_config: un evento por fila (turno confirmado, recordatorio, vehículo listo, caso derivado) con la plantilla
  aprobada por Meta que usa y si está prendido.
- whatsapp_envios: cada aviso, con el id que devuelve YCloud para seguir si se entregó, se leyó o falló.
Ver docs/avisos-whatsapp.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "avisos_config",
        sa.Column("evento", sa.String(30), primary_key=True),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("plantilla", sa.String(120), nullable=True),
        sa.Column("idioma", sa.String(10), nullable=False, server_default="es"),
        sa.Column("hora", sa.String(5), nullable=True),
        sa.Column("actualizado_por", sa.String(160), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "whatsapp_envios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evento", sa.String(30), nullable=False),
        sa.Column("destino", sa.String(20), nullable=True),
        sa.Column("nombre", sa.String(160), nullable=True),
        sa.Column("plantilla", sa.String(120), nullable=True),
        sa.Column("idioma", sa.String(10), nullable=True),
        sa.Column("variables", sa.JSON(), nullable=True),
        sa.Column("estado", sa.String(16), nullable=False, server_default="pendiente"),
        sa.Column("error", sa.String(400), nullable=True),
        sa.Column("ycloud_id", sa.String(80), nullable=True),
        sa.Column("turno_id", sa.Integer(), sa.ForeignKey("taller_turnos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("caso_id", sa.Integer(), sa.ForeignKey("casos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("prueba", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("enviado_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_whatsapp_envios_ycloud_id", "whatsapp_envios", ["ycloud_id"])
    op.create_index("ix_whatsapp_envios_turno_evento", "whatsapp_envios", ["turno_id", "evento"])
    op.create_index("ix_whatsapp_envios_created_at", "whatsapp_envios", ["created_at"])


def downgrade() -> None:
    for indice in ("ix_whatsapp_envios_created_at", "ix_whatsapp_envios_turno_evento", "ix_whatsapp_envios_ycloud_id"):
        op.drop_index(indice, table_name="whatsapp_envios")
    op.drop_table("whatsapp_envios")
    op.drop_table("avisos_config")
