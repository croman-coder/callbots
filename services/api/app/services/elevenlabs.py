"""Cliente de ElevenLabs para el panel: las voces de la cuenta, una muestra y cuánto se gastó.

Quien habla en las llamadas es el voice-agent, con su propio cliente. Acá solo se lee la cuenta (qué
voces hay) y se genera una muestra para escuchar antes de elegir.

ElevenLabs cobra por carácter: la lista y el consumo se guardan unos minutos y las muestras se recuerdan,
para que abrir el panel o tocar ▶ dos veces no gaste créditos dos veces.
"""

from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PREFIJO = "el:"
RAPIDO = 15.0
LENTO = 60.0
_VIGENCIA = 300.0
_ID_VOZ = re.compile(r"[A-Za-z0-9]{10,40}")


class ElevenLabsError(RuntimeError):
    """ElevenLabs no está disponible o rechazó el pedido. El mensaje se puede mostrar."""


_cache: dict[str, tuple[float, object]] = {}
_muestras: "OrderedDict[tuple[str, str], bytes]" = OrderedDict()
MAX_MUESTRAS = 40


def is_configured() -> bool:
    return bool(settings.elevenlabs_api_key)


def id_callbot(voz_id: str) -> str:
    return f"{PREFIJO}{voz_id}"


def id_elevenlabs(voice_profile_id: str | None) -> str | None:
    """"el:abc…" → "abc…" (solo si parece un id de voz); cualquier otra cosa → None."""
    if voice_profile_id and voice_profile_id.startswith(PREFIJO):
        voz = voice_profile_id[len(PREFIJO):]
        return voz if _ID_VOZ.fullmatch(voz) else None
    return None


def _cabeceras() -> dict[str, str]:
    return {"xi-api-key": settings.elevenlabs_api_key, "Accept": "application/json"}


def _fallo(accion: str, exc: Exception) -> ElevenLabsError:
    respuesta = getattr(exc, "response", None)
    if respuesta is not None:
        log.warning("ElevenLabs, %s: HTTP %s %s", accion, respuesta.status_code, respuesta.text[:200])
        if respuesta.status_code in (401, 403):
            return ElevenLabsError(
                f"ElevenLabs no dejó {accion}: la clave no tiene ese permiso o venció. Revisala en elevenlabs.io."
            )
        if respuesta.status_code == 429:
            return ElevenLabsError("ElevenLabs pide esperar un momento (demasiados pedidos). Probá de nuevo.")
        return ElevenLabsError(f"ElevenLabs no pudo {accion} ({respuesta.status_code}).")
    log.warning("ElevenLabs, %s: %s", accion, exc)
    return ElevenLabsError(f"No se pudo conectar con ElevenLabs para {accion}. Probá de nuevo en un rato.")


def _con_cache(clave: str, obtener):
    ahora = time.monotonic()
    guardado = _cache.get(clave)
    if guardado and ahora - guardado[0] < _VIGENCIA:
        return guardado[1]
    valor = obtener()
    _cache[clave] = (ahora, valor)
    return valor


def olvidar() -> None:
    """Fuerza a volver a pedir la lista (por ejemplo, después de agregar una voz en ElevenLabs)."""
    _cache.clear()


def listar_voces() -> list[dict]:
    """Las voces de «Mis voces» de la cuenta (incluye las que se agregaron de la biblioteca)."""

    def pedir() -> list[dict]:
        try:
            r = httpx.get(
                "https://api.elevenlabs.io/v1/voices", headers=_cabeceras(), timeout=RAPIDO
            )
            r.raise_for_status()
            return list(r.json().get("voices") or [])
        except (httpx.HTTPError, ValueError) as exc:
            raise _fallo("leer las voces", exc) from exc

    return _con_cache("voces", pedir)  # type: ignore[return-value]


def consumo() -> dict | None:
    """Caracteres usados y disponibles del período, o None si la clave no deja leer la suscripción."""

    def pedir() -> dict | None:
        try:
            r = httpx.get("https://api.elevenlabs.io/v1/user/subscription", headers=_cabeceras(), timeout=RAPIDO)
            r.raise_for_status()
            d = r.json()
            return {
                "usados": int(d.get("character_count", 0)),
                "limite": int(d.get("character_limit", 0)),
                "plan": str(d.get("tier") or ""),
            }
        except (httpx.HTTPError, ValueError, TypeError):
            return None  # una clave restringida puede no tener este permiso: no es un error

    return _con_cache("consumo", pedir)  # type: ignore[return-value]


def muestra(voz_id: str, texto: str) -> bytes:
    """MP3 para escuchar. Gasta créditos (un carácter por letra del texto), así que se recuerda."""
    if not _ID_VOZ.fullmatch(voz_id):
        raise ElevenLabsError("Esa voz no existe.")
    clave = (voz_id, texto)
    if clave in _muestras:
        _muestras.move_to_end(clave)
        return _muestras[clave]
    try:
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voz_id}",
            params={"output_format": "mp3_44100_64"},
            json={"text": texto, "model_id": settings.elevenlabs_model},
            headers={**_cabeceras(), "Accept": "audio/mpeg"},
            timeout=LENTO,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise _fallo("generar la muestra", exc) from exc
    _muestras[clave] = r.content
    while len(_muestras) > MAX_MUESTRAS:
        _muestras.popitem(last=False)
    return r.content
