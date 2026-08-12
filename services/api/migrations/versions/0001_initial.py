"""Esquema inicial del callbot

Revision ID: 0001
Revises:
Create Date: 2026-08-12
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Los enums se crean explícitamente para poder reusarlos en varias columnas
# sin que Alembic intente crear el tipo dos veces.
question_type = postgresql.ENUM(
    "SCALE_1_5", "SCALE_1_10", "YES_NO", "NUMERIC", "OPEN",
    name="question_type",
    create_type=False,
)
target_status = postgresql.ENUM(
    "PENDING", "SCHEDULED", "QUEUED", "CALLING", "COMPLETED",
    "NO_ANSWER", "FAILED", "OPTED_OUT", "SKIPPED",
    name="target_status",
    create_type=False,
)
call_outcome = postgresql.ENUM(
    "COMPLETED", "PARTIAL", "NO_ANSWER", "BUSY", "REJECTED", "FAILED",
    name="call_outcome",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    question_type.create(bind, checkfirst=True)
    target_status.create(bind, checkfirst=True)
    call_outcome.create(bind, checkfirst=True)

    # ---------------------------------------------------------------- campaigns
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("bitrix_entity_type_id", sa.Integer(), nullable=False),
        sa.Column("trigger_field", sa.String(length=120), nullable=False),
        sa.Column("delay_hours", sa.Integer(), nullable=False),
        sa.Column("extra_filter", sa.JSON(), nullable=True),
        sa.Column("call_window_start", sa.String(length=5), nullable=True),
        sa.Column("call_window_end", sa.String(length=5), nullable=True),
        sa.Column("call_window_days", sa.String(length=20), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("retry_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("intro_script", sa.Text(), nullable=True),
        sa.Column("outro_script", sa.Text(), nullable=True),
        sa.Column("fallback_script", sa.Text(), nullable=True),
        sa.Column("optout_script", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---------------------------------------------------------------- questions
    op.create_table(
        "questions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("qtype", question_type, nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=True),
        sa.Column("max_answer_seconds", sa.Integer(), nullable=True),
        sa.Column("max_retries", sa.Integer(), nullable=True),
        sa.Column("counts_for_score", sa.Boolean(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "position", name="uq_question_position"),
    )

    # ----------------------------------------------------------- survey_targets
    op.create_table(
        "survey_targets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("bitrix_entity_type_id", sa.Integer(), nullable=False),
        sa.Column("bitrix_entity_id", sa.Integer(), nullable=False),
        sa.Column("bitrix_contact_id", sa.Integer(), nullable=True),
        sa.Column("bitrix_title", sa.String(length=255), nullable=True),
        sa.Column("contact_name", sa.String(length=160), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("trigger_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invoice_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", target_status, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "campaign_id", "bitrix_entity_id", "trigger_at",
            name="uq_target_entity_trigger",
        ),
    )
    op.create_index("ix_survey_targets_bitrix_entity_id", "survey_targets", ["bitrix_entity_id"])
    op.create_index("ix_survey_targets_phone", "survey_targets", ["phone"])
    op.create_index("ix_survey_targets_scheduled_at", "survey_targets", ["scheduled_at"])
    op.create_index("ix_survey_targets_status", "survey_targets", ["status"])
    # El scheduler consulta siempre por (status, scheduled_at): índice compuesto
    op.create_index("ix_target_status_scheduled", "survey_targets", ["status", "scheduled_at"])

    # ------------------------------------------------------------ call_attempts
    op.create_table(
        "call_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_uuid", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=True),
        sa.Column("asterisk_channel_id", sa.String(length=80), nullable=True),
        sa.Column("dialed_number", sa.String(length=40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("outcome", call_outcome, nullable=True),
        sa.Column("hangup_cause", sa.String(length=80), nullable=True),
        sa.Column("questions_asked", sa.Integer(), nullable=True),
        sa.Column("questions_answered", sa.Integer(), nullable=True),
        sa.Column("bitrix_call_id", sa.String(length=80), nullable=True),
        sa.Column("recording_path", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["target_id"], ["survey_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_uuid"),
    )
    op.create_index("ix_call_attempts_session_uuid", "call_attempts", ["session_uuid"])

    # ------------------------------------------------------------------ answers
    op.create_table(
        "answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("asr_confidence", sa.Float(), nullable=True),
        sa.Column("audio_path", sa.String(length=400), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("value_source", sa.String(length=20), nullable=True),
        sa.Column("retries_used", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["call_id"], ["call_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "question_id", name="uq_answer_call_question"),
    )

    # ------------------------------------------------------------ call_analyses
    op.create_table(
        "call_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("call_id", sa.Integer(), nullable=False),
        sa.Column("satisfaction_score", sa.Float(), nullable=True),
        sa.Column("sentiment", sa.String(length=20), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("topics", sa.JSON(), nullable=True),
        sa.Column("requires_followup", sa.Boolean(), nullable=True),
        sa.Column("followup_reason", sa.Text(), nullable=True),
        sa.Column("model_used", sa.String(length=80), nullable=True),
        sa.Column("synced_to_bitrix", sa.Boolean(), nullable=True),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["call_id"], ["call_attempts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id"),
    )
    # retry_failed_writebacks filtra por este campo cada 10 minutos
    op.create_index(
        "ix_call_analyses_synced", "call_analyses", ["synced_to_bitrix", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("call_analyses")
    op.drop_table("answers")
    op.drop_table("call_attempts")
    op.drop_table("survey_targets")
    op.drop_table("questions")
    op.drop_table("campaigns")

    bind = op.get_bind()
    call_outcome.drop(bind, checkfirst=True)
    target_status.drop(bind, checkfirst=True)
    question_type.drop(bind, checkfirst=True)
