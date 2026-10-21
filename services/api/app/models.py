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
    VOICEMAIL = "voicemail"      # atendió un contestador, no una persona
    TRANSFERRED = "transferred"  # el cliente pidió un asesor y se le pasó la llamada


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

    # Contexto para las respuestas conversacionales durante la llamada.
    conversation_prompt: Mapped[str | None] = mapped_column(Text)

    # Instrucciones extra para el LLM que analiza la llamada. Se suman al
    # prompt base, que ya fija el contrato JSON que la aplicación parsea.
    analysis_prompt: Mapped[str | None] = mapped_column(Text)
    # Datos propios que Claude saca de cada llamada, además de los fijos (ver services/variables.py):
    # [{"clave", "nombre", "tipo", "descripcion", "opciones"}].
    analysis_fields: Mapped[list | None] = mapped_column(JSON)
    # Palabras que Whisper tiene que reconocer en lo que dice el cliente (marcas, modelos, sucursales), una por línea.
    # Medido el 21/10/2026: sin ellas escribía bien 8 de 17 nombres; con ellas, 17 de 17 (ver docs/vocabulario.md).
    vocabulario: Mapped[str | None] = mapped_column(Text)
    # Bases de conocimiento que usa el agente para responder (ids de Brain, hasta 5; ver app/conocimiento.py).
    brain_ids: Mapped[list | None] = mapped_column(JSON)
    # Campos de Bitrix que viajan como variables del guion: {"modelo": "ufCrm5_1700000000"}.
    variables_bitrix: Mapped[dict | None] = mapped_column(JSON)
    # Webhooks de resultados (app/webhooks.py): [{"url": ..., "activo": bool}] y el secreto con que se firman.
    webhooks: Mapped[list | None] = mapped_column(JSON)
    webhook_secreto: Mapped[str | None] = mapped_column(String(64))

    # --- Recepción (app/recepcion.py) ---
    # «encuesta»: el agente llama y pregunta. «recepcion»: atiende cuando el cliente llama y deriva a un área.
    modo: Mapped[str] = mapped_column(String(12), default="encuesta", server_default="encuesta", nullable=False)
    # Áreas a las que deriva: [{"clave", "nombre", "atiende", "responsable", "correo", "plazo_horas"}].
    areas: Mapped[list | None] = mapped_column(JSON)
    # Lo que el agente no puede decir ni prometer («no dar precios», «no diagnosticar fallas»…), una regla por línea.
    limites: Mapped[str | None] = mapped_column(Text)
    # Sistemas que consulta en vivo en cada turno (app/fuentes/): {"repuestos": bool, "precios": bool}.
    fuentes: Mapped[dict | None] = mapped_column(JSON)

    # --- Voz del bot ---
    # Se mandan al voice-agent en el guion de cada llamada, así se ajustan
    # desde el panel sin redesplegar nada.
    voice_speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    voice_pitch: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    voice_expressiveness: Mapped[float] = mapped_column(
        Float, default=0.667, nullable=False
    )
    voice_volume: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    # Lo que se escribe en la pestaña «Contexto» de la ficha del agente. De acá sale (con Claude) el
    # texto de `conversation_prompt`, que es lo que el agente sabe al conversar.
    empresa_nombre: Mapped[str | None] = mapped_column(String(160))
    empresa_descripcion: Mapped[str | None] = mapped_column(Text)
    instrucciones_importantes: Mapped[str | None] = mapped_column(Text)

    # Qué voz usa esta campaña. Vacío = la del servidor (VOICEBOX_PROFILE_ID, o Piper si no
    # hay); "piper" = Piper a propósito; otro valor = el id de un perfil de Voicebox.
    voice_profile_id: Mapped[str | None] = mapped_column(String(64))

    # --- Buzón de voz ---
    # None = no se detecta (la llamada sigue como siempre). "colgar" = si atiende un contestador se
    # corta; "mensaje" = se espera el tono y se deja `voicemail_script`.
    voicemail_action: Mapped[str | None] = mapped_column(String(10))
    voicemail_script: Mapped[str] = mapped_column(
        Text,
        default=(
            "Hola, le hablamos de Santa Rosa por una breve encuesta sobre su visita al taller. "
            "Lo vamos a volver a llamar. Muchas gracias."
        ),
    )

    # --- Transferir a un asesor ---
    # Teléfono (E.164) al que se pasa la llamada cuando el cliente pide hablar con una persona.
    # Vacío = no se transfiere.
    transfer_number: Mapped[str | None] = mapped_column(String(30))
    transfer_script: Mapped[str] = mapped_column(
        Text, default="Claro, un momento que lo comunico con un asesor."
    )

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


class PruebaAgente(Base):
    """Una prueba del agente hablando desde el navegador (pestaña «Probar agente»).

    No es una llamada real: no toca destinatarios ni resultados. Guarda lo que se dijo y cómo le
    pareció a quien probó, para mejorar el guion con lo que se escuchó.
    """

    __tablename__ = "pruebas_agente"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usuario: Mapped[str] = mapped_column(String(160), nullable=False)
    iniciada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    duracion_seconds: Mapped[int | None] = mapped_column(Integer)
    valoracion: Mapped[str | None] = mapped_column(String(10))  # "bien" | "mal"
    transcripcion: Mapped[list | None] = mapped_column(JSON, default=list)


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
    # Opcionales: un destinatario puede venir de una carga manual o de otro
    # sistema. Sin registro asociado no hay writeback, y el resultado queda
    # solo en el panel.
    bitrix_entity_type_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    bitrix_contact_id: Mapped[int | None] = mapped_column(Integer)
    bitrix_title: Mapped[str | None] = mapped_column(String(255))

    contact_name: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(40), index=True)

    # Datos extra del cliente para el guion ({{modelo}}, {{sucursal}}…): columnas del Excel o campos de Bitrix
    # (ver app/datos_cliente.py).
    datos: Mapped[dict | None] = mapped_column(JSON)

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
    # Revisión a mano del resultado (app/revision.py): "exitosa" | "no_exitosa"; vacío = como la calificó el sistema.
    revision: Mapped[str | None] = mapped_column(String(12))
    revision_motivo: Mapped[str | None] = mapped_column(Text)
    revision_por: Mapped[str | None] = mapped_column(String(160))
    revision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    # Cómo se obtuvo value_numeric: "rules" (regex/palabras clave), "llm" o "manual" (corregido en el panel)
    value_source: Mapped[str | None] = mapped_column(String(20))
    # Si alguien corrigió el valor a mano: el que había antes, quién y cuándo (app/revision.py).
    valor_original: Mapped[float | None] = mapped_column(Float)
    corregido_por: Mapped[str | None] = mapped_column(String(160))
    corregido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
    # Quién le devolvió la llamada al cliente. Mientras esté vacío, el cliente sigue en la lista
    # de "para volver a llamar" del tablero.
    followup_done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    followup_done_by: Mapped[str | None] = mapped_column(String(160))
    followup_note: Mapped[str | None] = mapped_column(Text)
    # Valores de los datos propios del agente, por clave (ver Campaign.analysis_fields).
    extras: Mapped[dict | None] = mapped_column(JSON)

    model_used: Mapped[str | None] = mapped_column(String(80))
    synced_to_bitrix: Mapped[bool] = mapped_column(Boolean, default=False)
    sync_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    call: Mapped[CallAttempt] = relationship(back_populates="analysis")


class Pronunciation(Base):
    """Cómo tiene que decir el bot una palabra (ver app/pronunciaciones.py)."""

    __tablename__ = "pronunciations"

    id: Mapped[int] = mapped_column(primary_key=True)
    palabra: Mapped[str] = mapped_column(String(60), nullable=False)
    dicho: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentVersion(Base):
    """Una versión guardada de la configuración de un agente (ver app/versiones.py): sirve para ver qué cambió, quién
    y cuándo, y para volver atrás."""

    __tablename__ = "agent_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(160))
    resumen: Mapped[str] = mapped_column(String(400), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class Brain(Base):
    """Una base de conocimiento: documentos y texto con los que el agente responde (catálogo, precios, sucursales,
    preguntas frecuentes). Se comparte entre agentes (ver app/conocimiento.py)."""

    __tablename__ = "brains"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    fuentes: Mapped[list["BrainSource"]] = relationship(
        back_populates="brain", cascade="all, delete-orphan", order_by="BrainSource.id"
    )


class BrainSource(Base):
    """Una fuente de una base de conocimiento: texto pegado, un archivo o una página web, ya pasada a texto."""

    __tablename__ = "brain_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    brain_id: Mapped[int] = mapped_column(ForeignKey("brains.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(10), nullable=False)  # texto | archivo | web
    titulo: Mapped[str] = mapped_column(String(200), nullable=False)
    origen: Mapped[str | None] = mapped_column(String(500))  # nombre del archivo o dirección de la página
    contenido: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    brain: Mapped[Brain] = relationship(back_populates="fuentes")


class WebhookEnvio(Base):
    """Un envío de un webhook de resultados: a dónde, qué respondió y cuánto tardó."""

    __tablename__ = "webhook_envios"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    call_id: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    estado: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    duracion_ms: Mapped[int | None] = mapped_column(Integer)
    prueba: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CallComment(Base):
    """Un comentario del equipo sobre una llamada («le devolví la llamada», «el ASR entendió mal»…)."""

    __tablename__ = "call_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    autor: Mapped[str] = mapped_column(String(160), nullable=False)
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Caso(Base):
    """Una consulta o un reclamo que entró por un agente de recepción (ver app/recepcion.py).

    Reemplaza la planilla compartida del call center: cada caso tiene área, responsable con nombre y apellido y un
    plazo para contestar. Calidad ve en la bandeja lo que está abierto y lo que se venció.
    """

    __tablename__ = "casos"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    # «telefono» (llamó de verdad), «prueba» (desde el navegador), «chat» (texto) o «simulacion» (la IA hizo de cliente).
    origen: Mapped[str] = mapped_column(String(12), nullable=False)
    session_uuid: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    telefono: Mapped[str | None] = mapped_column(String(30))
    nombre: Mapped[str | None] = mapped_column(String(160))
    motivo: Mapped[str | None] = mapped_column(String(12))  # consulta | reclamo | turno | estado | otro
    urgente: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    resumen: Mapped[str | None] = mapped_column(Text)
    datos: Mapped[dict | None] = mapped_column(JSON)  # modelo, chapa, sucursal… lo que dijo el cliente
    conversacion: Mapped[list | None] = mapped_column(JSON)  # [{"quien": "agente"|"cliente", "texto", "at"}]
    # El área al derivar, copiada tal como estaba: si después cambia el responsable, el caso no se mueve solo.
    area: Mapped[str | None] = mapped_column(String(40))
    area_nombre: Mapped[str | None] = mapped_column(String(80))
    responsable: Mapped[str | None] = mapped_column(String(120))
    correo_responsable: Mapped[str | None] = mapped_column(String(160))
    plazo_horas: Mapped[int | None] = mapped_column(Integer)
    # en_llamada | abierto | respondido | cerrado | vacio (cortó sin decir nada)
    estado: Mapped[str] = mapped_column(String(12), default="en_llamada", server_default="en_llamada", nullable=False, index=True)
    vence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    derivado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    respondido_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cerrado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    respuesta: Mapped[str | None] = mapped_column(Text)
    eventos: Mapped[list | None] = mapped_column(JSON)  # [{"at", "por", "que", "texto"}]: el recorrido del caso
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    campaign: Mapped[Campaign] = relationship()


class TallerSucursal(Base):
    """Un taller que recibe vehículos: horario por día, bahías y cuántos autos recibe a la misma hora (app/agenda.py)."""

    __tablename__ = "taller_sucursales"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(80), nullable=False)
    direccion: Mapped[str | None] = mapped_column(String(200))
    activa: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    # {"0": ["07:00", "17:00"], …, "5": ["07:00", "11:00"]}: lunes = 0. Un día sin entrada está cerrado.
    horario: Mapped[dict | None] = mapped_column(JSON)
    intervalo_min: Mapped[int] = mapped_column(Integer, default=30, server_default="30", nullable=False)
    bahias: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    recepciones_por_franja: Mapped[int] = mapped_column(Integer, default=2, server_default="2", nullable=False)
    # Lo que se suma a los tiempos del servicio para decirle al cliente desde qué hora lo retira.
    margen_retiro_min: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    # Cuánto antes como mínimo se puede pedir un turno para hoy.
    anticipacion_min: Mapped[int] = mapped_column(Integer, default=120, server_default="120", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TallerServicio(Base):
    """Un servicio con sus tiempos por etapa. Con marca o modelo, pisa al general para esos vehículos."""

    __tablename__ = "taller_servicios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    palabras: Mapped[str | None] = mapped_column(String(300))  # cómo lo pide la gente: «service de los 10 mil»
    marca: Mapped[str | None] = mapped_column(String(40))
    modelo: Mapped[str | None] = mapped_column(String(60))
    recepcion_min: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    trabajo_min: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    lavado_min: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    calidad_min: Mapped[int] = mapped_column(Integer, default=5, server_default="5", nullable=False)
    entrega_min: Mapped[int] = mapped_column(Integer, default=10, server_default="10", nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def total_min(self) -> int:
        return self.recepcion_min + self.trabajo_min + self.lavado_min + self.calidad_min + self.entrega_min


class TallerTurno(Base):
    """Un turno: a qué hora deja el vehículo, en qué bahía se trabaja y desde cuándo lo retira."""

    __tablename__ = "taller_turnos"

    id: Mapped[int] = mapped_column(primary_key=True)
    sucursal_id: Mapped[int] = mapped_column(ForeignKey("taller_sucursales.id", ondelete="RESTRICT"), nullable=False)
    servicio_id: Mapped[int | None] = mapped_column(ForeignKey("taller_servicios.id", ondelete="SET NULL"))
    servicio_nombre: Mapped[str] = mapped_column(String(120), nullable=False)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # recepción
    trabajo_desde: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trabajo_hasta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retiro_estimado: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bahia: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    cliente_nombre: Mapped[str | None] = mapped_column(String(160))
    telefono: Mapped[str | None] = mapped_column(String(30))
    marca: Mapped[str | None] = mapped_column(String(40))
    modelo: Mapped[str | None] = mapped_column(String(60))
    chapa: Mapped[str | None] = mapped_column(String(20))
    km: Mapped[int | None] = mapped_column(Integer)
    # agendado | confirmado | en_taller | listo | entregado | cancelado | no_vino | prueba
    estado: Mapped[str] = mapped_column(String(12), default="agendado", server_default="agendado", nullable=False)
    origen: Mapped[str] = mapped_column(String(12), default="panel", server_default="panel", nullable=False)
    caso_id: Mapped[int | None] = mapped_column(ForeignKey("casos.id", ondelete="SET NULL"))
    notas: Mapped[str | None] = mapped_column(Text)
    eventos: Mapped[list | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    sucursal: Mapped[TallerSucursal] = relationship()


class AvisoConfig(Base):
    """Qué se avisa por WhatsApp y con qué plantilla aprobada por Meta (app/avisos.py)."""

    __tablename__ = "avisos_config"

    evento: Mapped[str] = mapped_column(String(30), primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    plantilla: Mapped[str | None] = mapped_column(String(120))
    idioma: Mapped[str] = mapped_column(String(10), default="es", server_default="es", nullable=False)
    hora: Mapped[str | None] = mapped_column(String(5))  # recordatorio: a qué hora del día anterior
    actualizado_por: Mapped[str | None] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WhatsappEnvio(Base):
    """Un aviso por WhatsApp: a quién, con qué plantilla y qué pasó (enviado, entregado, leído, falló)."""

    __tablename__ = "whatsapp_envios"

    id: Mapped[int] = mapped_column(primary_key=True)
    evento: Mapped[str] = mapped_column(String(30), nullable=False)
    destino: Mapped[str | None] = mapped_column(String(20))  # +595…
    nombre: Mapped[str | None] = mapped_column(String(160))
    plantilla: Mapped[str | None] = mapped_column(String(120))
    idioma: Mapped[str | None] = mapped_column(String(10))
    variables: Mapped[list | None] = mapped_column(JSON)
    # pendiente | enviado | entregado | leido | fallo | sin_configurar | apagado | sin_telefono
    estado: Mapped[str] = mapped_column(String(16), default="pendiente", server_default="pendiente", nullable=False)
    error: Mapped[str | None] = mapped_column(String(400))
    ycloud_id: Mapped[str | None] = mapped_column(String(80), index=True)
    turno_id: Mapped[int | None] = mapped_column(ForeignKey("taller_turnos.id", ondelete="SET NULL"))
    caso_id: Mapped[int | None] = mapped_column(ForeignKey("casos.id", ondelete="SET NULL"))
    prueba: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    enviado_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
