"""Cliente de Voicebox para el panel.

Solo lo que el panel necesita: listar perfiles, clonar una voz y generar una
muestra. Quien sintetiza durante las llamadas es el voice-agent, que tiene su
propio cliente con caché — este no cachea nada a propósito.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

# La clonación procesa audio y la generación corre un modelo pesado: los dos
# tardan bastante más que una request web normal.
CLONE_TIMEOUT = 300.0
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


def clone_voice(
    name: str,
    audio_bytes: bytes,
    filename: str,
    reference_text: str,
    language: str = "es",
) -> dict:
    """Crea el perfil y le sube la muestra de referencia. Devuelve el perfil.

    Son dos pasos en Voicebox: primero el perfil vacío, después el audio que lo
    convierte en un clon. Si el segundo falla, el perfil queda creado y hay que
    borrarlo — por eso el mensaje de error lo menciona.
    """
    base = _base()

    try:
        response = httpx.post(
            f"{base}/profiles",
            json={
                "name": name,
                "description": "Voz del callbot de encuestas",
                "language": language,
                "voice_type": "cloned",
            },
            timeout=QUICK_TIMEOUT,
        )
        response.raise_for_status()
        profile = response.json()
    except httpx.HTTPError as exc:
        raise _fail("No se pudo crear el perfil", exc) from exc

    profile_id = profile["id"]

    try:
        response = httpx.post(
            f"{base}/profiles/{profile_id}/samples",
            files={"file": (filename, audio_bytes, "audio/wav")},
            data={"reference_text": reference_text},
            timeout=CLONE_TIMEOUT,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise VoiceboxError(
            f"El perfil {profile_id} se creó pero el audio no se pudo procesar: "
            f"{getattr(getattr(exc, 'response', None), 'text', str(exc))[:200]}. "
            f"Borralo desde Voicebox antes de reintentar."
        ) from exc

    log.info("Voz clonada en Voicebox: %s (%s)", name, profile_id)
    return profile


def generate(profile_id: str, text: str, engine: str | None = None) -> bytes:
    """Devuelve el WAV generado. /generate/stream no guarda nada en disco."""
    try:
        response = httpx.post(
            f"{_base()}/generate/stream",
            json={
                "profile_id": profile_id,
                "text": text,
                "language": "es",
                "engine": engine or settings.voicebox_engine,
                "normalize": True,
            },
            timeout=GENERATE_TIMEOUT,
        )
        response.raise_for_status()
        return response.content
    except httpx.HTTPError as exc:
        raise _fail("No se pudo generar el audio", exc) from exc
