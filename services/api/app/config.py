"""Configuración central. Todo se lee del entorno (12-factor)."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infra ---
    database_url: str = "postgresql+psycopg://callbot:callbot@postgres:5432/callbot"
    redis_url: str = "redis://redis:6379/0"
    log_level: str = "INFO"
    tz: str = "America/Asuncion"

    admin_user: str = "admin"
    admin_password: str = "admin"
    # Token compartido con el voice-agent. Protege /internal/* de escrituras
    # ajenas si el puerto de la API queda expuesto.
    internal_token: str = "dev-internal-token"

    recordings_dir: str = "/recordings"

    # AudioSocket del voice-agent. Lo usa el simulador del panel para hacer de
    # puente con el micrófono del navegador.
    audiosocket_host: str = "voice-agent"
    audiosocket_port: int = 8090

    # --- Bitrix24 ---
    bitrix_webhook_url: str = ""
    bitrix_entity_type_id: int = 2
    bitrix_field_workshop_entry: str = "ufCrm5FechaIngresoTaller"
    bitrix_field_invoice_date: str = ""
    bitrix_field_delivery_date: str = ""
    bitrix_field_contact_id: str = "contactId"
    bitrix_field_score_writeback: str = ""
    bitrix_timeline_comment: bool = True
    bitrix_register_call: bool = True
    bitrix_telephony_user_id: int = 1

    # --- Reglas de la encuesta ---
    survey_delay_hours: int = 48
    call_window_start: time = time(9, 0)
    call_window_end: time = time(19, 0)
    call_window_days: str = "0,1,2,3,4,5"
    max_call_attempts: int = 3
    retry_interval_minutes: int = 180
    bitrix_poll_interval_minutes: int = 15
    # Cuántas llamadas simultáneas como máximo (limitado por los canales de la troncal)
    max_concurrent_calls: int = 3
    # Si el canal no llega al voice-agent en este tiempo, se da por no atendida
    call_answer_timeout_seconds: int = 90

    # --- Asterisk / ARI ---
    ari_url: str = "http://asterisk:8088"
    ari_user: str = "callbot"
    ari_password: str = ""
    ari_app: str = "callbot"
    # Plantilla del endpoint a marcar. {number} = teléfono en E.164 sin '+'.
    #   DEV  (softphone): "PJSIP/softphone-1"
    #   PROD (troncal):   "PJSIP/{number}@trunk-proveedor"
    asterisk_dial_template: str = "PJSIP/softphone-1"
    asterisk_context: str = "callbot-outbound"
    asterisk_extension: str = "start"
    asterisk_callerid: str = "Callbot <1000>"
    asterisk_dial_timeout: int = 45

    # --- Voicebox (solo para el diagnóstico y los scripts de clonación;
    #     quien sintetiza es el voice-agent) ---
    voicebox_url: str = ""
    voicebox_profile_id: str = ""
    voicebox_engine: str = "qwen"

    # --- Gemini (respuestas conversacionales) ---
    # Vacío = el bot usa la frase fija cuando no entiende, como siempre.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Corto a propósito: del otro lado hay alguien esperando en silencio.
    gemini_timeout_seconds: float = 4.0

    # --- Ollama ---
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_enabled: bool = True

    @field_validator("call_window_start", "call_window_end", mode="before")
    @classmethod
    def _parse_time(cls, v: object) -> object:
        """Acepta 'HH:MM' además del formato ISO completo."""
        if isinstance(v, str) and len(v) == 5 and ":" in v:
            hh, mm = v.split(":")
            return time(int(hh), int(mm))
        return v

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def allowed_weekdays(self) -> set[int]:
        """Días de la semana habilitados: 0=lunes ... 6=domingo."""
        return {
            int(d.strip())
            for d in self.call_window_days.split(",")
            if d.strip().isdigit()
        }

    @property
    def bitrix_base(self) -> str:
        return self.bitrix_webhook_url.rstrip("/") + "/"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
