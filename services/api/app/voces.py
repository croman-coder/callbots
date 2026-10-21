"""Catálogo de voces para el selector de la ficha del agente (como el «Seleccionar voz» de Dapta).

Todas son gratis: corren en el servidor (Voicebox y Piper). Lo que cambia es cómo suenan y si se
pueden generar en vivo, que importa para las frases que no se conocen de antemano (el saludo con el
nombre del cliente, lo que contesta Claude).
"""

from __future__ import annotations

import logging
import re

from app.config import settings
from app.services import voicebox, voicestudio

log = logging.getLogger(__name__)

MOTORES = {
    "kokoro": {"nombre": "Kokoro 82M", "detalle": "Rápida: habla en vivo, sin esperas", "en_vivo": True},
    "chatterbox": {"nombre": "Chatterbox multilingüe", "detalle": "Voz clonada, muy natural", "en_vivo": False},
    "qwen": {"nombre": "Qwen3-TTS", "detalle": "Voz clonada, muy natural", "en_vivo": False},
    "qwen_custom_voice": {"nombre": "Qwen3-TTS", "detalle": "Voz prediseñada", "en_vivo": False},
    "piper": {"nombre": "Piper", "detalle": "Respaldo: suena robótica", "en_vivo": True},
    "omnivoice": {"nombre": "OmniVoice", "detalle": "Voz clonada, habla en vivo", "en_vivo": True},
}
GENERO_PRESET = {"ef_dora": "Mujer", "em_alex": "Hombre", "em_santa": "Hombre"}

# Lo que lee en voz alta la persona cuya voz se clona. Tiene el tono de una llamada (saludo, pregunta,
# agradecimiento) porque el clon copia también la forma de hablar, y dura unos 15 segundos.
TEXTO_REFERENCIA = (
    "Hola, buenas tardes. Le hablo de Santa Rosa Automotores. "
    "Lo llamamos para contarle que su vehículo ya está listo y que puede pasar a retirarlo cuando guste. "
    "¿Tiene alguna consulta? Con mucho gusto lo ayudo. ¡Muchas gracias y que tenga un excelente día!"
)


def frase_consentimiento(persona: str) -> str:
    """Lo que la persona dice (y queda grabado) para autorizar el uso de su voz."""
    return (
        f"Yo, {persona}, autorizo a Santa Rosa Paraguay a usar mi voz para las llamadas automáticas "
        "de Callbot, y sé que puedo pedir que la borren cuando quiera."
    )


def _genero(perfil: dict) -> str:
    preset = GENERO_PRESET.get(str(perfil.get("preset_voice_id") or ""))
    if preset:
        return preset
    texto = f"{perfil.get('name', '')} {perfil.get('description') or ''}".lower()
    if re.search(r"\b(mujer|femenin)", texto):
        return "Mujer"
    if re.search(r"\b(hombre|masculin)", texto):
        return "Hombre"
    return "—"


def _nombre_limpio(nombre: str) -> str:
    """«Alex (Kokoro, hombre)» → «Alex»: el motor y el género ya van en sus columnas."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", nombre or "").strip() or nombre


def catalogo() -> tuple[list[dict], str | None]:
    """Las voces en español que se pueden elegir, y un error legible si Voicebox no respondió."""
    perfiles: list[dict] = []
    error = None
    if voicebox.is_configured():
        try:
            perfiles = voicebox.list_profiles()
        except voicebox.VoiceboxError as exc:
            error = "No se pudieron leer las voces del servidor de voz. Probá de nuevo en un rato."
            log.warning("Catálogo de voces: %s", exc)

    voces: list[dict] = []
    for p in perfiles:
        if (p.get("language") or "es") != "es":
            continue
        motor = voicebox.engine_de(p)
        datos = MOTORES.get(motor, {"nombre": motor, "detalle": "", "en_vivo": False})
        # Los motores de clonación (Qwen, Chatterbox) en el procesador tardan minutos por frase y
        # al cargarse pueden tumbar Voicebox: no se ofrecen hasta que la voz tenga GPU.
        if not datos["en_vivo"] and not settings.voces_con_gpu:
            continue
        clonada = p.get("voice_type") == "cloned"
        voces.append(
            {
                "id": p["id"],
                "nombre": _nombre_limpio(str(p.get("name", ""))),
                "genero": _genero(p),
                "tipo": "Clonada" if clonada else "Prediseñada",
                "descripcion": p.get("description") or "",
                "motor": motor,
                "motor_nombre": datos["nombre"],
                "detalle": datos["detalle"],
                "en_vivo": datos["en_vivo"],
                "predeterminada": p["id"] == settings.voicebox_profile_id,
                "escuchar": True,
            }
        )
    voces.extend(clonadas())
    # Primero las que hablan en vivo (no cambian de voz en mitad de la llamada), después por nombre.
    voces.sort(key=lambda v: (not v["en_vivo"], v["nombre"].lower()))
    voces.append(
        {
            "id": "piper",
            "nombre": "Daniela",
            "genero": "Mujer",
            "tipo": "Prediseñada",
            "descripcion": "Voz de respaldo, acento rioplatense",
            "motor": "piper",
            "motor_nombre": MOTORES["piper"]["nombre"],
            "detalle": MOTORES["piper"]["detalle"],
            "en_vivo": True,
            "predeterminada": False,
            "escuchar": False,
        }
    )
    return voces, error


def clonadas() -> list[dict]:
    """Las voces clonadas en VoiceStudio con consentimiento registrado: sin él no se ofrecen."""
    if not voicestudio.is_configured():
        return []
    try:
        perfiles = voicestudio.list_profiles()
    except voicestudio.VoiceStudioError as exc:
        log.warning("Voces clonadas: %s", exc)
        return []
    return [
        {
            "id": voicestudio.id_callbot(str(p["id"])),
            "nombre": _nombre_limpio(str(p.get("name") or "Sin nombre")),
            "genero": _genero(p),
            "tipo": "Clonada",
            "descripcion": p.get("description") or "",
            "motor": "omnivoice",
            "motor_nombre": MOTORES["omnivoice"]["nombre"],
            "detalle": MOTORES["omnivoice"]["detalle"],
            "en_vivo": True,
            "predeterminada": False,
            "escuchar": True,
            "consentimiento": p.get("consent_text") or "",
            "creada": p.get("created_at"),
        }
        for p in perfiles
        if p.get("id") and p.get("kind", "clone") == "clone" and p.get("verified_own_voice")
    ]


def elegida(voces: list[dict], voice_profile_id: str | None) -> dict | None:
    """La voz que usa el agente: la suya, o la predeterminada del servidor si no eligió ninguna."""
    buscada = voice_profile_id or settings.voicebox_profile_id or "piper"
    return next((v for v in voces if v["id"] == buscada), None)
