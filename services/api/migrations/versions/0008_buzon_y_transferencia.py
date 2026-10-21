"""Buzón de voz y transferencia a un asesor

Revision ID: 0008
Revises: 0007

Dos resultados nuevos de llamada (`voicemail`, `transferred`) y, por agente, qué hacer cuando atiende
un contestador y a qué número pasar la llamada si el cliente pide un asesor. Todo apagado por defecto.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MENSAJE_BUZON = (
    "Hola, le hablamos de Santa Rosa por una breve encuesta sobre su visita al taller. "
    "Lo vamos a volver a llamar. Muchas gracias."
)
FRASE_TRANSFERENCIA = "Claro, un momento que lo comunico con un asesor."


def upgrade() -> None:
    # El enum nativo de Postgres guarda los NOMBRES de los miembros (en mayúsculas). Agregar un valor
    # no se puede usar en la misma transacción que lo crea: va en su propio bloque.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE call_outcome ADD VALUE IF NOT EXISTS 'VOICEMAIL'")
        op.execute("ALTER TYPE call_outcome ADD VALUE IF NOT EXISTS 'TRANSFERRED'")

    op.add_column("campaigns", sa.Column("voicemail_action", sa.String(10), nullable=True))
    op.add_column("campaigns", sa.Column("voicemail_script", sa.Text(), nullable=False, server_default=MENSAJE_BUZON))
    op.add_column("campaigns", sa.Column("transfer_number", sa.String(30), nullable=True))
    op.add_column("campaigns", sa.Column("transfer_script", sa.Text(), nullable=False, server_default=FRASE_TRANSFERENCIA))


def downgrade() -> None:
    op.drop_column("campaigns", "transfer_script")
    op.drop_column("campaigns", "transfer_number")
    op.drop_column("campaigns", "voicemail_script")
    op.drop_column("campaigns", "voicemail_action")
    # Postgres no permite sacar un valor de un enum: VOICEMAIL y TRANSFERRED quedan en el tipo, sin uso.
