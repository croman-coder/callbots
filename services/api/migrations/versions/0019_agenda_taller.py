"""Agenda del taller: sucursales con horario y bahías, servicios con tiempos por etapa y turnos

Revision ID: 0019
Revises: 0018

Pedido de la dirección regional de posventa (21/10/2026): que el agente de recepción agende el service con los
tiempos reales (recepción, trabajo, lavado, control de calidad, entrega), por marca y modelo si hace falta.
Ver docs/agenda-taller.md.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "taller_sucursales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("direccion", sa.String(200), nullable=True),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("horario", sa.JSON(), nullable=True),
        sa.Column("intervalo_min", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("bahias", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("recepciones_por_franja", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("margen_retiro_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("anticipacion_min", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "taller_servicios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("palabras", sa.String(300), nullable=True),
        sa.Column("marca", sa.String(40), nullable=True),
        sa.Column("modelo", sa.String(60), nullable=True),
        sa.Column("recepcion_min", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("trabajo_min", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("lavado_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calidad_min", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("entrega_min", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "taller_turnos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sucursal_id", sa.Integer(), sa.ForeignKey("taller_sucursales.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("servicio_id", sa.Integer(), sa.ForeignKey("taller_servicios.id", ondelete="SET NULL"), nullable=True),
        sa.Column("servicio_nombre", sa.String(120), nullable=False),
        sa.Column("inicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trabajo_desde", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trabajo_hasta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retiro_estimado", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bahia", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cliente_nombre", sa.String(160), nullable=True),
        sa.Column("telefono", sa.String(30), nullable=True),
        sa.Column("marca", sa.String(40), nullable=True),
        sa.Column("modelo", sa.String(60), nullable=True),
        sa.Column("chapa", sa.String(20), nullable=True),
        sa.Column("km", sa.Integer(), nullable=True),
        sa.Column("estado", sa.String(12), nullable=False, server_default="agendado"),
        sa.Column("origen", sa.String(12), nullable=False, server_default="panel"),
        sa.Column("caso_id", sa.Integer(), sa.ForeignKey("casos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("notas", sa.Text(), nullable=True),
        sa.Column("eventos", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_taller_turnos_sucursal_inicio", "taller_turnos", ["sucursal_id", "inicio"])
    op.create_index("ix_taller_turnos_estado", "taller_turnos", ["estado"])

    # Punto de partida, editable en Agenda → Talleres y servicios. El taller y su horario son los que tiene cargados
    # el Cotizador (Taller Zeballos: 3 bahías, de 07:00 a 18:00 cada hora, de lunes a viernes); el sábado queda
    # cerrado hasta que alguien lo cargue. El service de 10.000 km tiene los tiempos que dio la dirección de
    # posventa; los demás, la duración de los tipos del Cotizador.
    sucursales = sa.table(
        "taller_sucursales", sa.column("nombre", sa.String), sa.column("horario", sa.JSON),
        sa.column("intervalo_min", sa.Integer), sa.column("bahias", sa.Integer),
        sa.column("recepciones_por_franja", sa.Integer), sa.column("margen_retiro_min", sa.Integer),
        sa.column("anticipacion_min", sa.Integer),
    )
    op.bulk_insert(sucursales, [{
        "nombre": "Taller Zeballos", "horario": {str(d): ["07:00", "18:00"] for d in range(5)},
        "intervalo_min": 60, "bahias": 3, "recepciones_por_franja": 2, "margen_retiro_min": 60, "anticipacion_min": 120,
    }])
    servicios = sa.table(
        "taller_servicios", sa.column("nombre", sa.String), sa.column("palabras", sa.String),
        sa.column("recepcion_min", sa.Integer), sa.column("trabajo_min", sa.Integer), sa.column("lavado_min", sa.Integer),
        sa.column("calidad_min", sa.Integer), sa.column("entrega_min", sa.Integer),
    )
    op.bulk_insert(servicios, [
        {"nombre": "Service 10.000 km", "palabras": "service de los 10 mil, diez mil kilómetros, primer service",
         "recepcion_min": 5, "trabajo_min": 45, "lavado_min": 30, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Mantenimiento programado", "palabras": "service, mantenimiento, cambio de aceite y filtros",
         "recepcion_min": 5, "trabajo_min": 90, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Service general", "palabras": "service completo, revisión general",
         "recepcion_min": 5, "trabajo_min": 120, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Diagnóstico", "palabras": "ruido, falla, luz en el tablero, revisar algo",
         "recepcion_min": 5, "trabajo_min": 60, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Reparación", "palabras": "arreglo, cambio de piezas",
         "recepcion_min": 5, "trabajo_min": 180, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Garantía", "palabras": "falla en garantía, campaña, llamado a revisión",
         "recepcion_min": 5, "trabajo_min": 90, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
        {"nombre": "Chapería y pintura", "palabras": "choque, abolladura, rayón, pintura",
         "recepcion_min": 5, "trabajo_min": 240, "lavado_min": 0, "calidad_min": 5, "entrega_min": 10},
    ])


def downgrade() -> None:
    op.drop_index("ix_taller_turnos_estado", table_name="taller_turnos")
    op.drop_index("ix_taller_turnos_sucursal_inicio", table_name="taller_turnos")
    op.drop_table("taller_turnos")
    op.drop_table("taller_servicios")
    op.drop_table("taller_sucursales")
