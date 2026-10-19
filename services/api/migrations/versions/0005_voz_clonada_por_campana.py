"""voz clonada por campaña

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Vacío = la voz por defecto del servidor (VOICEBOX_PROFILE_ID, o Piper si no hay).
    # "piper" = Piper a propósito. Cualquier otro valor = el id de un perfil de Voicebox.
    op.add_column("campaigns", sa.Column("voice_profile_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "voice_profile_id")
