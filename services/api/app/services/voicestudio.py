"""Cliente de VoiceStudio (OmniVoice) para el panel: clonar voces y escucharlas.

Quien habla en las llamadas es el voice-agent, con su propio cliente. Acá solo se crean los
perfiles (con el consentimiento de la persona), se listan y se genera una muestra.

Las voces de VoiceStudio llevan el prefijo "vs:" en Callbot para no confundirse con las de Voicebox.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PREFIJO = "vs:"
RAPIDO = 10.0
# Crear el perfil procesa el audio de referencia; la muestra corre el modelo. Con GPU es cuestión de
# segundos, pero la primera vez que se carga el modelo tarda más.
LENTO = 120.0


class VoiceStudioError(RuntimeError):
    """VoiceStudio no está disponible o rechazó la operación. El mensaje se puede mostrar."""


def is_configured() -> bool:
    return bool(settings.voicestudio_url)


def _url(ruta: str) -> str:
    if not settings.voicestudio_url:
        raise VoiceStudioError("El motor de voces clonadas no está instalado en el servidor.")
    return f"{settings.voicestudio_url.rstrip('/')}{ruta}"


def _fallo(accion: str, exc: Exception) -> VoiceStudioError:
    respuesta = getattr(exc, "response", None)
    if respuesta is not None:
        try:
            detalle = respuesta.json().get("detail") or respuesta.text
        except ValueError:
            detalle = respuesta.text
        log.warning("VoiceStudio, %s: HTTP %s %s", accion, respuesta.status_code, str(detalle)[:300])
        return VoiceStudioError(f"El motor de voz no pudo {accion} ({respuesta.status_code}).")
    log.warning("VoiceStudio, %s: %s", accion, exc)
    return VoiceStudioError(f"El motor de voz no respondió al {accion}. Probá de nuevo en un rato.")


def _cabeceras() -> dict[str, str]:
    clave = settings.voicestudio_api_key
    return {"Authorization": f"Bearer {clave}"} if clave else {}


def id_callbot(perfil_id: str) -> str:
    return f"{PREFIJO}{perfil_id}"


def id_estudio(voice_profile_id: str) -> str | None:
    """"vs:abc" → "abc"; cualquier otra cosa → None."""
    if voice_profile_id and voice_profile_id.startswith(PREFIJO):
        return voice_profile_id[len(PREFIJO):] or None
    return None


def list_profiles() -> list[dict]:
    try:
        r = httpx.get(_url("/profiles"), headers=_cabeceras(), timeout=RAPIDO)
        r.raise_for_status()
        datos = r.json()
    except httpx.HTTPError as exc:
        raise _fallo("leer las voces", exc) from exc
    return datos if isinstance(datos, list) else []


def clonar(
    nombre: str,
    referencia: bytes,
    referencia_archivo: str,
    referencia_texto: str,
    consentimiento: bytes,
    consentimiento_archivo: str,
    consentimiento_texto: str,
) -> dict:
    """Crea el perfil y le registra el consentimiento. Si el consentimiento no queda, se borra el perfil:
    una voz clonada sin permiso registrado no se puede usar."""
    try:
        r = httpx.post(
            _url("/profiles"),
            data={"name": nombre, "ref_text": referencia_texto, "language": "Spanish", "kind": "clone"},
            files={"ref_audio": (referencia_archivo, referencia, "application/octet-stream")},
            headers=_cabeceras(),
            timeout=LENTO,
        )
        r.raise_for_status()
        perfil = r.json()
    except httpx.HTTPError as exc:
        raise _fallo("crear la voz", exc) from exc

    try:
        r = httpx.post(
            _url(f"/profiles/{perfil['id']}/consent"),
            data={"consent_text": consentimiento_texto},
            files={"consent_audio": (consentimiento_archivo, consentimiento, "audio/wav")},
            headers=_cabeceras(),
            timeout=RAPIDO,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        borrar(perfil["id"], silencioso=True)
        raise _fallo("guardar el consentimiento", exc) from exc
    return perfil


def borrar(perfil_id: str, silencioso: bool = False) -> None:
    try:
        httpx.delete(_url(f"/profiles/{perfil_id}"), headers=_cabeceras(), timeout=RAPIDO).raise_for_status()
    except httpx.HTTPError as exc:
        if not silencioso:
            raise _fallo("borrar la voz", exc) from exc
        log.warning("No se pudo borrar el perfil %s de VoiceStudio: %s", perfil_id, exc)


def muestra(perfil_id: str, texto: str) -> bytes:
    """WAV para escuchar en el navegador."""
    try:
        r = httpx.post(
            _url("/v1/audio/speech"),
            json={
                "model": "omnivoice",
                "input": texto,
                "voice": perfil_id,
                "response_format": "wav",
                "language": "Spanish",
            },
            headers=_cabeceras(),
            timeout=LENTO,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise _fallo("generar la muestra", exc) from exc
    return r.content
