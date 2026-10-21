"""Cliente de VoiceStudio (OmniVoice) para el panel: clonar voces y escucharlas.

Quien habla en las llamadas es el voice-agent, con su propio cliente. Acá solo se crean los
perfiles (con el consentimiento de la persona), se listan y se genera una muestra.

Las voces de VoiceStudio llevan el prefijo "vs:" en Callbot para no confundirse con las de Voicebox.
"""

from __future__ import annotations

import io
import logging
import wave

import httpx

from app.config import settings

log = logging.getLogger(__name__)

PREFIJO = "vs:"
# Se guarda en el campo `personality` del perfil: así se distingue una voz creada con IA (que no es de ninguna
# persona y no necesita permiso) de una clonada de alguien (que solo se ofrece con su permiso grabado).
MARCA_IA = "callbot-ia"
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


def duracion_wav(contenido: bytes) -> float | None:
    """Segundos de un WAV PCM, o None si no es un WAV que se pueda leer (se confía en la verificación)."""
    try:
        with wave.open(io.BytesIO(contenido), "rb") as w:
            return w.getnframes() / float(w.getframerate()) if w.getframerate() else None
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


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

    # Se prueba la voz ahora, con una frase corta: es cuando el motor codifica la grabación (lo que más
    # memoria de la placa pide) y lo que deja la voz lista. Si no entra, se descubre acá y no en una llamada.
    try:
        verificar(perfil["id"])
    except VoiceStudioError as exc:
        borrar(perfil["id"], silencioso=True)
        raise VoiceStudioError(
            "No se pudo preparar la voz con esa grabación. Probá con una más corta (unos 5 segundos), "
            "clara y sin ruido de fondo."
        ) from exc
    return perfil


def disenar(instruccion: str, semilla: int, texto: str) -> bytes:
    """WAV de una voz inventada por el modelo a partir de rasgos («female, young adult, moderate pitch»).
    Sale con los pasos de calidad máxima: este audio es el que después se fija como la voz."""
    try:
        r = httpx.post(
            _url("/v1/audio/speech"),
            json={"model": "omnivoice", "input": texto, "voice": "default", "response_format": "wav",
                  "language": "Spanish", "instruct": instruccion, "seed": semilla, "num_step": 32},
            headers=_cabeceras(),
            timeout=LENTO,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise _fallo("inventar la voz", exc) from exc
    return r.content


def crear_disenada(nombre: str, audio: bytes, texto: str) -> dict:
    """Fija una voz diseñada: el audio que se escuchó pasa a ser su referencia, así suena igual en todas las frases.
    No es la voz de una persona, no lleva permiso; queda marcada con `MARCA_IA`."""
    try:
        r = httpx.post(
            _url("/profiles"),
            data={"name": nombre, "ref_text": texto, "language": "Spanish", "kind": "clone", "personality": MARCA_IA},
            files={"ref_audio": ("voz-ia.wav", audio, "application/octet-stream")},
            headers=_cabeceras(),
            timeout=LENTO,
        )
        r.raise_for_status()
        perfil = r.json()
    except httpx.HTTPError as exc:
        raise _fallo("guardar la voz", exc) from exc
    try:
        verificar(perfil["id"])
    except VoiceStudioError as exc:
        borrar(perfil["id"], silencioso=True)
        raise VoiceStudioError("No se pudo preparar la voz. Probá con otra variante.") from exc
    return perfil


def verificar(perfil_id: str) -> None:
    """Genera una frase corta con la voz: si anda, el perfil está listo para hablar en vivo."""
    try:
        r = httpx.post(
            _url("/v1/audio/speech"),
            json={"model": "omnivoice", "input": "Hola, buenas tardes.", "voice": perfil_id,
                  "response_format": "wav", "language": "Spanish", "num_step": 12},
            headers=_cabeceras(),
            timeout=LENTO,
        )
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise _fallo("probar la voz", exc) from exc


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
