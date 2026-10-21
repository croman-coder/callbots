"""Cliente de Voicebox para el panel.

Solo lo que el panel necesita: listar perfiles y generar una muestra. Clonar
voces ahora es de VoiceStudio (ver voicestudio.py). Quien sintetiza durante las
llamadas es el voice-agent, que tiene su propio cliente con caché — este no
cachea nada a propósito.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# La generación corre un modelo pesado: tarda bastante más que una request web normal.
GENERATE_TIMEOUT = 300.0
QUICK_TIMEOUT = 10.0


class VoiceboxError(RuntimeError):
    """Voicebox no está disponible o rechazó la operación."""


def _base() -> str:
    if not settings.voicebox_url:
        raise VoiceboxError(
            "VOICEBOX_URL no está configurado. Sin eso el bot habla con Piper."
        )
    return settings.voicebox_url.rstrip("/")


def _fail(action: str, exc: Exception) -> VoiceboxError:
    detail = getattr(exc, "response", None)
    if detail is not None:
        return VoiceboxError(f"{action}: HTTP {detail.status_code} — {detail.text[:200]}")
    return VoiceboxError(f"{action}: {exc}")


def is_configured() -> bool:
    return bool(settings.voicebox_url)


def health() -> bool:
    try:
        httpx.get(f"{_base()}/health", timeout=QUICK_TIMEOUT).raise_for_status()
        return True
    except (httpx.HTTPError, VoiceboxError):
        return False


def list_profiles() -> list[dict]:
    try:
        response = httpx.get(f"{_base()}/profiles", timeout=QUICK_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise _fail("No se pudieron listar los perfiles", exc) from exc


def engine_de(profile: dict) -> str:
    """Con qué motor habla un perfil.

    Una voz preset (Kokoro, Qwen CustomVoice) solo funciona con SU motor y Voicebox
    rechaza cualquier otro con un 400; una clonada usa el motor de clonación
    (Qwen por defecto). Sin esto, elegir una voz preset en el panel daba error.
    """
    if profile.get("voice_type") == "preset" and profile.get("preset_engine"):
        return str(profile["preset_engine"])
    return str(profile.get("default_engine") or settings.voicebox_engine)


def _engine_del_perfil(profile_id: str) -> str:
    try:
        response = httpx.get(f"{_base()}/profiles/{profile_id}", timeout=QUICK_TIMEOUT)
        response.raise_for_status()
        return engine_de(response.json())
    except httpx.HTTPError as exc:
        raise _fail("No se pudo leer el perfil", exc) from exc


def generate(profile_id: str, text: str, engine: str | None = None) -> bytes:
    """Devuelve el WAV generado. /generate/stream no guarda nada en disco."""
    engine = engine or _engine_del_perfil(profile_id)
    try:
        response = httpx.post(
            f"{_base()}/generate/stream",
            json={
                "profile_id": profile_id,
                "text": text,
                "language": "es",
                "engine": engine,
                "normalize": True,
            },
            timeout=GENERATE_TIMEOUT,
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise _fail("No se pudo generar el audio", exc) from exc
