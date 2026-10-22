"""Sonido de fondo del agente: ambiente de oficina y teclado mientras busca o anota

Revision ID: 0023
Revises: 0022

- campaigns.sonido_fondo: "oficina" o nada. Un ambiente suave de oficina por debajo de toda la llamada, para que no
  suene a una línea muerta entre frase y frase.
- campaigns.teclado: si suena un teclado mientras el agente busca en el sistema o anota (tapa la espera).
Pedido de Croman (22/10/2026). Ver docs/escucha-activa.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("sonido_fondo", sa.String(length=20), nullable=True))
    op.add_column("campaigns", sa.Column("teclado", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("campaigns", "teclado")
    op.drop_column("campaigns", "sonido_fondo")
