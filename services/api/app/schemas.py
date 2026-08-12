"""Esquemas Pydantic: contrato entre la API, el voice-agent y el panel."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import CallOutcome, QuestionType, TargetStatus


# ---------------------------------------------------------------------------
# Guion que consume el voice-agent
# ---------------------------------------------------------------------------
class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    text: str
    qtype: QuestionType
    is_required: bool
    max_answer_seconds: int
    max_retries: int


class SurveyScript(BaseModel):
    """Todo lo que el agente necesita para conducir la llamada."""

    session_uuid: uuid.UUID
    call_id: int | None = None
    demo: bool = False
    campaign_id: int
    campaign_name: str
    contact_name: str | None = None
    intro_script: str
    outro_script: str
    fallback_script: str
    optout_script: str
    questions: list[QuestionOut]


# ---------------------------------------------------------------------------
# Reportes del voice-agent hacia la API
# ---------------------------------------------------------------------------
class SessionStarted(BaseModel):
    answered_at: datetime | None = None


class AnswerIn(BaseModel):
    question_id: int
    transcript: str | None = None
    asr_confidence: float | None = None
    audio_path: str | None = None
    duration_seconds: float | None = None
    retries_used: int = 0


class AnswerResult(BaseModel):
    """La API interpreta y le dice al agente si puede seguir o repreguntar."""

    understood: bool
    value: float | None = None
    is_optout: bool = False
    is_silence: bool = False
    should_retry: bool = False
    saved: bool = True


class SessionFinished(BaseModel):
    outcome: CallOutcome = CallOutcome.COMPLETED
    questions_asked: int = 0
    questions_answered: int = 0
    hangup_cause: str | None = None
    recording_path: str | None = None


# ---------------------------------------------------------------------------
# CRUD del panel
# ---------------------------------------------------------------------------
class QuestionCreate(BaseModel):
    text: str = Field(min_length=3)
    qtype: QuestionType = QuestionType.SCALE_1_5
    position: int | None = None
    is_required: bool = True
    max_answer_seconds: int = 20
    max_retries: int = 1
    counts_for_score: bool = True
    is_active: bool = True


class QuestionUpdate(BaseModel):
    text: str | None = None
    qtype: QuestionType | None = None
    position: int | None = None
    is_required: bool | None = None
    max_answer_seconds: int | None = None
    max_retries: int | None = None
    counts_for_score: bool | None = None
    is_active: bool | None = None


class CampaignCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = None
    bitrix_entity_type_id: int
    trigger_field: str
    delay_hours: int = 48
    extra_filter: dict | None = None
    call_window_start: str = "09:00"
    call_window_end: str = "19:00"
    call_window_days: str = "0,1,2,3,4,5"
    max_attempts: int = 3
    retry_interval_minutes: int = 180
    intro_script: str | None = None
    outro_script: str | None = None
    fallback_script: str | None = None
    optout_script: str | None = None
    is_active: bool = True


class CampaignUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    bitrix_entity_type_id: int | None = None
    trigger_field: str | None = None
    delay_hours: int | None = None
    extra_filter: dict | None = None
    call_window_start: str | None = None
    call_window_end: str | None = None
    call_window_days: str | None = None
    max_attempts: int | None = None
    retry_interval_minutes: int | None = None
    intro_script: str | None = None
    outro_script: str | None = None
    fallback_script: str | None = None
    optout_script: str | None = None
    is_active: bool | None = None


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    is_active: bool
    bitrix_entity_type_id: int
    trigger_field: str
    delay_hours: int
    call_window_start: str
    call_window_end: str
    call_window_days: str
    max_attempts: int
    created_at: datetime
    questions: list[QuestionOut] = []


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    bitrix_entity_id: int
    contact_name: str | None
    phone: str | None
    trigger_at: datetime
    scheduled_at: datetime
    status: TargetStatus
    attempts: int
    last_error: str | None


class AnswerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    question_id: int
    transcript: str | None
    value_numeric: float | None
    value_source: str | None
    asr_confidence: float | None


class CallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_uuid: uuid.UUID
    target_id: int
    attempt_number: int
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    outcome: CallOutcome | None
    questions_asked: int
    questions_answered: int
    answers: list[AnswerOut] = []


class StatsOut(BaseModel):
    total_targets: int
    pending: int
    completed: int
    no_answer: int
    failed: int
    calls_today: int
    avg_satisfaction: float | None
    response_rate: float
