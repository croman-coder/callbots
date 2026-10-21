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

    # Acceso desde el CRM (app/sso.py). Secreto compartido con el CRM, que firma
    # los tickets. Vacío o menor a 32 caracteres = desactivado: no existe /sso y
    # ninguna cookie de sesión vale. Es el estado por defecto, a propósito.
    sso_secret: str = Field("", validation_alias="CALLBOT_SSO_SECRET")
    # Quién puede enmarcar el panel en un iframe (CSP frame-ancestors). Separados
    # por espacio. Sin esto cualquier página ajena podría enmarcarlo y montarle
    # botones encima al operador que ya tiene sesión abierta (clickjacking).
    frame_ancestors: str = "https://crm.santarosa.lat"

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
    # Dónde escucha el voice-agent (AudioSocket). La prueba desde el navegador se conecta ahí como si
    # fuera Asterisk: ver routers/agente.py.
    voice_agent_host: str = "voice-agent"
    voice_agent_port: int = 8090
    asterisk_dial_timeout: int = 45

    # --- Voicebox (solo para el diagnóstico y los scripts de clonación;
    #     quien sintetiza es el voice-agent) ---
    voicebox_url: str = ""
    voicebox_profile_id: str = ""
    voicebox_engine: str = "qwen"
    # Con GPU para la voz se pueden ofrecer las voces clonadas (Qwen, Chatterbox) en el selector.
    voces_con_gpu: bool = Field(False, validation_alias="VOCES_CON_GPU")
    # VoiceStudio (OmniVoice): clonar voces desde el panel y hacerlas hablar en vivo. Necesita GPU.
    voicestudio_url: str = ""

    # --- Login con la cuenta de los dashboards (Supabase) ---
    # Es la misma cuenta que usan Meta Ads y el tablero comercial: una persona, un
    # usuario. La clave anónima es pública por diseño (viaja a todo navegador); lo
    # que protege los datos es el RLS de Supabase, no esconderla.
    supabase_url: str = Field("", validation_alias="SUPABASE_URL")
    supabase_anon_key: str = Field("", validation_alias="SUPABASE_ANON_KEY")
    # Dominios de correo que pueden entrar (coma). Toda la empresa usa santarosa.com.py.
    login_dominios: str = Field("santarosa.com.py", validation_alias="LOGIN_DOMINIOS")
    # Qué rol de los dashboards entra a Callbot y con qué rol. Lo que no figura, no entra:
    # las encuestas llevan teléfonos de clientes, así que `brand` y `administracion` quedan fuera.
    login_roles: str = Field("super_admin:admin,manager:calidad", validation_alias="LOGIN_ROLES")

    # --- Claude (Anthropic) ---
    # El mismo modelo y la misma clave que usan Santa Rosa Ads, el asistente de los
    # tableros y la Agencia IA. Es la única IA de Callbot: conversación en llamada, análisis
    # posterior, instrucciones del agente, contexto de la empresa y consultas sobre las llamadas.
    # Sin clave, el bot usa las frases fijas y el análisis queda solo con el puntaje por reglas.
    anthropic_api_key: str = Field("", validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = Field("claude-haiku-5-5", validation_alias="CALLBOT_LLM_MODEL")
    # En llamada hay alguien escuchando: pasados estos segundos se usa la frase fija.
    llm_timeout_seconds: float = Field(8.0, validation_alias="CALLBOT_LLM_TIMEOUT")
    # El análisis corre después de colgar, sin apuro.
    llm_analysis_timeout_seconds: float = 60.0


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
