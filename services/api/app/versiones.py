"""Historial de versiones de un agente: qué cambió, quién y cuándo, y volver atrás.

Cada vez que se guardan cambios en la configuración de un agente se registra una versión con la configuración completa de
ese momento. Si es la primera, antes se guarda el estado anterior como punto de partida, así también se puede volver a
lo que había antes de empezar a editar.

Qué entra: el texto que dice el agente (guion, contexto, instrucciones, análisis y sus variables), el horario, los
intentos, la voz, el buzón y la transferencia. Qué no: si el agente está activo o pausado, las preguntas (tienen su
propia pantalla y las respuestas ya dadas dependen de ellas) y los destinatarios.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AgentVersion, Campaign

MAX_VERSIONES = 60
MAX_TEXTO_DIFERENCIA = 600

# clave del modelo → cómo se llama en pantalla. El orden es el del resumen.
CAMPOS: dict[str, str] = {
    "name": "Nombre",
    "description": "Propósito",
    "empresa_nombre": "Empresa: nombre",
    "empresa_descripcion": "Empresa: descripción",
    "instrucciones_importantes": "Instrucciones importantes",
    "conversation_prompt": "Instrucciones del agente",
    "intro_script": "Guion: saludo",
    "outro_script": "Guion: cierre",
    "fallback_script": "Guion: cuando no se entiende",
    "optout_script": "Guion: cuando pide que no lo llamen",
    "analysis_prompt": "Análisis: objetivo",
    "analysis_fields": "Análisis: variables propias",
    "call_window_start": "Horario: desde",
    "call_window_end": "Horario: hasta",
    "call_window_days": "Días de llamada",
    "max_attempts": "Intentos",
    "retry_interval_minutes": "Minutos entre intentos",
    "delay_hours": "Demora desde el disparador (horas)",
    "bitrix_entity_type_id": "Bitrix: entityTypeId",
    "trigger_field": "Bitrix: campo disparador",
    "voice_profile_id": "Voz",
    "voice_speed": "Voz: velocidad",
    "voice_pitch": "Voz: tono",
    "voice_expressiveness": "Voz: expresividad",
    "voice_volume": "Voz: volumen",
    "voicemail_action": "Buzón de voz",
    "voicemail_script": "Buzón de voz: mensaje",
    "transfer_number": "Transferencia: teléfono del asesor",
    "transfer_script": "Transferencia: frase",
}


def instantanea(campana: Campaign) -> dict[str, Any]:
    """La configuración versionable de un agente, tal cual está ahora."""
    return {clave: getattr(campana, clave, None) for clave in CAMPOS}


def cambios(antes: dict[str, Any], despues: dict[str, Any]) -> list[str]:
    """Las claves que cambiaron, en el orden de CAMPOS. Una clave que falta en una versión vieja no cuenta como cambio."""
    return [c for c in CAMPOS if c in antes and c in despues and antes[c] != despues[c]]


def resumen(claves: list[str], maximo: int = 5) -> str:
    nombres = [CAMPOS[c] for c in claves]
    if len(nombres) > maximo:
        return "Cambió: " + ", ".join(nombres[:maximo]) + f" y {len(nombres) - maximo} más"
    return "Cambió: " + ", ".join(nombres)


def registrar(db: Session, campana: Campaign, antes: dict[str, Any], usuario: str | None, titulo: str | None = None) -> AgentVersion | None:
    """Guarda una versión si la configuración cambió respecto de `antes`. Llamar antes del commit, con `antes` tomado
    antes de aplicar los cambios. Devuelve la versión creada o None si no cambió nada."""
    despues = instantanea(campana)
    claves = cambios(antes, despues)
    if not claves:
        return None
    if campana.id is None:
        db.flush()
    hay = db.scalar(select(func.count()).select_from(AgentVersion).where(AgentVersion.campaign_id == campana.id)) or 0
    if hay == 0:
        db.add(AgentVersion(campaign_id=campana.id, created_by=None, snapshot=antes,
                            resumen="Cómo estaba antes del primer cambio guardado"))
    version = AgentVersion(campaign_id=campana.id, created_by=(usuario or None), snapshot=despues,
                           resumen=(titulo or resumen(claves))[:400])
    db.add(version)
    db.flush()
    # Se conservan las últimas MAX_VERSIONES: el historial no crece sin tope.
    sobran = db.scalars(
        select(AgentVersion.id).where(AgentVersion.campaign_id == campana.id).order_by(AgentVersion.id.desc()).offset(MAX_VERSIONES)
    ).all()
    if sobran:
        db.execute(delete(AgentVersion).where(AgentVersion.id.in_(sobran)))
    return version


def restaurar(db: Session, campana: Campaign, version: AgentVersion, usuario: str | None) -> list[str]:
    """Vuelve la configuración a la de `version` y deja constancia como una versión nueva (así restaurar también se
    puede deshacer). Devuelve las claves que cambiaron; vacío si ya estaba igual."""
    antes = instantanea(campana)
    for clave, valor in (version.snapshot or {}).items():
        if clave in CAMPOS:
            setattr(campana, clave, valor)
    fecha = _hora_local(version.created_at)
    registro = registrar(db, campana, antes, usuario, titulo=f"Volvió a la versión del {fecha}. " + resumen(cambios(antes, instantanea(campana))))
    return cambios(antes, registro.snapshot) if registro else []


def _hora_local(momento: datetime | None) -> str:
    """«21/10/2026 11:09» en hora de Paraguay. La base guarda UTC."""
    if not isinstance(momento, datetime):
        return "antes"
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.astimezone(ZoneInfo(settings.tz)).strftime("%d/%m/%Y %H:%M")


def texto(valor: Any) -> str:
    """Un valor de la configuración, legible y acotado, para mostrar en un cambio."""
    if valor is None or valor == "" or valor == []:
        return "(vacío)"
    if isinstance(valor, list):
        valor = "; ".join(str(v.get("nombre") or v) if isinstance(v, dict) else str(v) for v in valor)
    t = str(valor)
    return t if len(t) <= MAX_TEXTO_DIFERENCIA else t[:MAX_TEXTO_DIFERENCIA].rstrip() + "…"


def diferencias(antes: dict[str, Any] | None, despues: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(campo, antes, después) de lo que cambió entre dos versiones. Sin versión anterior no hay con qué comparar."""
    if antes is None:
        return []
    return [(CAMPOS[c], texto(antes.get(c)), texto(despues.get(c))) for c in cambios(antes, despues)]
