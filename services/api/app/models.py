"""Modelo de datos del callbot.

Flujo de una encuesta:

    SurveyTarget (agendado desde Bitrix)
        └── CallAttempt (1..N intentos de llamada)
              └── Answer (1 por pregunta respondida)
              └── CallAnalysis (1, generado por el LLM al terminar)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class QuestionType(str, enum.Enum):
    # Escala principal: acepta 0 a 10. Solo 9 y 10 se consideran satisfactorios,
    # el resto dispara advertencia (ver services/scoring.py::SATISFACTORY_MIN).
    # El identificador quedó como "scale_1_10" para no forzar una migración del
    # tipo enum de Postgres; el rango real siempre fue 0-10.
    SCALE_1_10 = "scale_1_10"
    SCALE_1_5 = "scale_1_5"      # heredada, se reescala a 0-10 para promediar
    YES_NO = "yes_no"            # sí = 10, no = 0
    NUMERIC = "numeric"
    OPEN = "open"                # respuesta libre, se guarda transcripta


class TargetStatus(str, enum.Enum):
    PENDING = "pending"          # detectado en Bitrix, esperando que venzan las 48hs
    SCHEDULED = "scheduled"      # vencido, esperando ventana horaria
    QUEUED = "queued"            # encolado para llamar
    CALLING = "calling"          # llamada en curso
    COMPLETED = "completed"      # encuesta respondida
    NO_ANSWER = "no_answer"      # agotó los intentos sin atender
    FAILED = "failed"            # error técnico
    OPTED_OUT = "opted_out"      # el cliente pidió no ser contactado
    SKIPPED = "skipped"          # descartado (sin teléfono, duplicado, etc.)


class CallOutcome(str, enum.Enum):
    COMPLETED = "completed"      # llegó al final del cuestionario
    PARTIAL = "partial"          # atendió pero cortó antes de terminar
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    REJECTED = "rejected"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Campaña y preguntas
# ---------------------------------------------------------------------------
class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # --- De dónde salen los destinatarios ---
    bitrix_entity_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Campo de Bitrix que marca el T0 del conteo (ej. fecha de ingreso al taller)
    trigger_field: Mapped[str] = mapped_column(String(120), nullable=False)
    delay_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    # Filtro adicional opcional para crm.item.list, ej. {"stageId": "DT1036_10:SUCCESS"}
    extra_filter: Mapped[dict | None] = mapped_column(JSON)

    # --- Reglas de llamada ---
    call_window_start: Mapped[str] = mapped_column(String(5), default="09:00")
    call_window_end: Mapped[str] = mapped_column(String(5), default="19:00")
    call_window_days: Mapped[str] = mapped_column(String(20), default="0,1,2,3,4,5")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_interval_minutes: Mapped[int] = mapped_column(Integer, default=180)

    # --- Guion ---
    intro_script: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hola, buenos días. Le hablamos del servicio de posventa. "
            "Estamos haciendo una breve encuesta de satisfacción sobre su última "
            "visita al taller. ¿Nos regala un minuto?"
        ),
    )
    outro_script: Mapped[str] = mapped_column(
        Text,
        default="Muchas gracias por su tiempo. Que tenga muy buen día.",
    )
    # Frase cuando no se entiende la respuesta
    fallback_script: Mapped[str] = mapped_column(
        Text, default="Disculpe, no llegué a escucharlo bien. ¿Me lo repite por favor?"
    )
    # Frase si el cliente dice que no puede atender
    optout_script: Mapped[str] = mapped_column(
        Text, default="Entiendo, no lo molesto más. Muchas gracias, buen día."
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    questions: Mapped[list[Question]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="Question.position",
    )
    targets: Mapped[list[SurveyTarget]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "position", name="uq_question_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    qtype: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="question_type"), default=QuestionType.SCALE_1_10
    )
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)
    max_answer_seconds: Mapped[int] = mapped_column(Integer, default=20)
    # Cuántas veces repreguntar si no se entiende antes de pasar a la siguiente
    max_retries: Mapped[int] = mapped_column(Integer, default=1)
    # Pesa en el cálculo del puntaje global de satisfacción
    counts_for_score: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    campaign: Mapped[Campaign] = relationship(back_populates="questions")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Destinatarios
# ---------------------------------------------------------------------------
class SurveyTarget(Base):
    __tablename__ = "survey_targets"
    __table_args__ = (
        UniqueConstraint(
            "campaign_id",
            "bitrix_entity_id",
            "trigger_at",
            name="uq_target_entity_trigger",
        ),
        Index("ix_target_status_scheduled", "status", "scheduled_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )

    # --- Origen en Bitrix ---
    bitrix_entity_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    bitrix_entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    bitrix_contact_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_title: Mapped[str | None] = mapped_column(String(255))

    contact_name: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40), index=True)

    # --- Fechas ---
    # T0: la fecha del campo disparador (ingreso al taller)
    trigger_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # T0 + delay_hours, ajustado a la ventana horaria
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # Fechas informativas que viajan al reporte
    invoice_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[TargetStatus] = mapped_column(
        Enum(TargetStatus, name="target_status"),
        default=TargetStatus.PENDING,
        nullable=False,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    campaign: Mapped[Campaign] = relationship(back_populates="targets")
    attempts_log: Mapped[list[CallAttempt]] = relationship(
        back_populates="target",
        cascade="all, delete-orphan",
        order_by="CallAttempt.created_at",
    )


# ---------------------------------------------------------------------------
# Llamadas
# ---------------------------------------------------------------------------
class CallAttempt(Base):
    __tablename__ = "call_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # UUID que viaja por AudioSocket y ata el canal de Asterisk con esta fila
    session_uuid: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False, index=True
    )
    target_id: Mapped[int] = mapped_column(
        ForeignKey("survey_targets.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)

    asterisk_channel_id: Mapped[str | None] = mapped_column(String(80))
    dialed_number: Mapped[str | None] = mapped_column(String(40))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    outcome: Mapped[CallOutcome | None] = mapped_column(
        Enum(CallOutcome, name="call_outcome")
    )
    hangup_cause: Mapped[str | None] = mapped_column(String(80))
    questions_asked: Mapped[int] = mapped_column(Integer, default=0)
    questions_answered: Mapped[int] = mapped_column(Integer, default=0)

    # ID que devuelve telephony.externalcall.register, para cerrar la llamada después
    bitrix_call_id: Mapped[str | None] = mapped_column(String(80))
    recording_path: Mapped[str | None] = mapped_column(String(400))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    target: Mapped[SurveyTarget] = relationship(back_populates="attempts_log")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="call", cascade="all, delete-orphan", order_by="Answer.id"
    )
    analysis: Mapped[CallAnalysis | None] = relationship(
        back_populates="call", cascade="all, delete-orphan", uselist=False
    )


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("call_id", "question_id", name="uq_answer_call_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("call_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )

    transcript: Mapped[str | None] = mapped_column(Text)
    # Confianza del ASR (0-1). Por debajo de ~0.6 conviene revisar a mano.
    asr_confidence: Mapped[float | None] = mapped_column(Float)
    audio_path: Mapped[str | None] = mapped_column(String(400))
    duration_seconds: Mapped[float | None] = mapped_column(Float)

    # Valor normalizado. Para scale_1_5 / scale_1_10 / numeric -> número.
    # Para yes_no -> 1.0 / 0.0. Para open -> None.
    value_numeric: Mapped[float | None] = mapped_column(Float)
    # Cómo se obtuvo value_numeric: "rules" (regex/palabras clave) o "llm"
    value_source: Mapped[str | None] = mapped_column(String(20))
    retries_used: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    call: Mapped[CallAttempt] = relationship(back_populates="answers")
    question: Mapped[Question] = relationship(back_populates="answers")


class CallAnalysis(Base):
    """Resultado del análisis post-llamada hecho por el LLM local."""

    __tablename__ = "call_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(
        ForeignKey("call_attempts.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # Promedio en escala 0-10 de las preguntas que puntúan.
    # >= 9 satisfactorio; por debajo, requires_followup queda en True.
    satisfaction_score: Mapped[float | None] = mapped_column(Float)
    sentiment: Mapped[str | None] = mapped_column(String(20))  # positivo|neutral|negativo
    summary: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list | None] = mapped_column(JSON)
    requires_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_reason: Mapped[str | None] = mapped_column(Text)

    model_used: Mapped[str | None] = mapped_column(String(80))
    synced_to_bitrix: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    call: Mapped[CallAttempt] = relationship(back_populates="analysis")
