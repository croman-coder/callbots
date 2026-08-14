"""Destinatarios que no vienen de Bitrix

Hasta acá `SurveyTarget` solo podía nacer del sync de Bitrix: las dos columnas
que lo vinculan al registro de origen eran NOT NULL, así que no había forma de
cargar a alguien a mano ni de traerlo de otro sistema.

En el portal de Santa Rosa los datos del taller no están en Bitrix, así que el
callbot tiene que poder funcionar sin esa dependencia. Se hacen opcionales; el
writeback ya sabe saltearse los destinatarios que no tienen registro asociado.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("survey_targets", "bitrix_entity_type_id", nullable=True)
    op.alter_column("survey_targets", "bitrix_entity_id", nullable=True)


def downgrade() -> None:
    # Volver atrás rompería cualquier destinatario cargado a mano, así que se
    # borran primero: son los únicos sin vínculo a Bitrix.
    op.execute("DELETE FROM survey_targets WHERE bitrix_entity_id IS NULL")
    op.alter_column("survey_targets", "bitrix_entity_id", nullable=False)
    op.alter_column("survey_targets", "bitrix_entity_type_id", nullable=False)
