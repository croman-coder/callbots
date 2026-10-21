"""Catálogo de voces para el selector de la ficha del agente (como el «Seleccionar voz» de Dapta).

Todas son gratis: corren en el servidor (Voicebox y Piper). Lo que cambia es cómo suenan y si se
pueden generar en vivo, que importa para las frases que no se conocen de antemano (el saludo con el
nombre del cliente, lo que contesta Claude).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time

from app.config import settings
from app.services import elevenlabs, voicebox, voicestudio

log = logging.getLogger(__name__)

MOTORES = {
    "kokoro": {"nombre": "Kokoro 82M", "detalle": "Rápida: habla en vivo, sin esperas", "en_vivo": True},
    "chatterbox": {"nombre": "Chatterbox multilingüe", "detalle": "Voz clonada, muy natural", "en_vivo": False},
    "qwen": {"nombre": "Qwen3-TTS", "detalle": "Voz clonada, muy natural", "en_vivo": False},
    "qwen_custom_voice": {"nombre": "Qwen3-TTS", "detalle": "Voz prediseñada", "en_vivo": False},
    "piper": {"nombre": "Piper", "detalle": "Respaldo: suena robótica", "en_vivo": True},
    "omnivoice": {"nombre": "OmniVoice", "detalle": "Voz clonada, habla en vivo", "en_vivo": True},
    "elevenlabs": {"nombre": "ElevenLabs", "detalle": "Muy natural. De pago, por carácter", "en_vivo": True},
}
GENERO_PRESET = {"ef_dora": "Mujer", "em_alex": "Hombre", "em_santa": "Hombre"}

# Lo que lee en voz alta la persona cuya voz se clona. Tiene el tono de una llamada (saludo y pregunta) porque
# el clon copia también la forma de hablar, y dura unos 5 segundos A PROPÓSITO: el audio de referencia se
# codifica en la placa de video y la memoria que pide crece con cada segundo (medido en la RTX 5070 que
# comparte con Whisper y gemma: 5 s dejan ~750 MB de margen, 7 s ~800 con gemma achicada y 8 s ya no entran).
# Más largo no clona mejor: el propio modelo recomienda entre 3 y 10 segundos.
TEXTO_REFERENCIA = "Hola, buenas tardes. Le hablo de Santa Rosa Automotores, ¿tiene un minutito?"
MAX_REFERENCIA_SEGUNDOS = 7.0


# Voces creadas con IA: el modelo inventa una voz a partir de rasgos. No es la voz de ninguna persona, así que
# no hace falta permiso, y es la forma de tener una voz natural y estable sin grabar a nadie. Los rasgos son
# listas cerradas (el motor los entiende en inglés) para que nadie pueda meter texto libre en el pedido.
DISENO_GENERO = {"Mujer": "female", "Hombre": "male"}
DISENO_EDAD = {"Joven": "young adult", "Adulta": "middle-aged"}
DISENO_TONO = {"Agudo": "high pitch", "Medio": "moderate pitch", "Grave": "low pitch"}
# Lo que dice la muestra, y la que queda como referencia de la voz: la de una llamada real, de unos 5 segundos.
TEXTO_DISENO = TEXTO_REFERENCIA


def instruccion_diseno(genero: str, edad: str, tono: str) -> str | None:
    """«female, young adult, moderate pitch», o None si algún rasgo no está en las listas."""
    g, e, t = DISENO_GENERO.get(genero), DISENO_EDAD.get(edad), DISENO_TONO.get(tono)
    return f"{g}, {e}, {t}" if g and e and t else None


# Las muestras que se escucharon y todavía no se guardaron. Quedan en el servidor y se guardan por su código: quien
# crea la voz solo puede quedarse con un audio que el motor generó, nunca con uno que suba él (eso sería clonar a
# alguien sin su permiso). Duran 15 minutos y no hay más de 20.
_MUESTRAS: dict[str, tuple[float, bytes]] = {}
_MUESTRAS_TTL = 15 * 60
_MUESTRAS_MAX = 20
_cerrojo_muestras = threading.Lock()


def guardar_muestra(wav: bytes, ahora: float | None = None) -> str:
    ahora = time.monotonic() if ahora is None else ahora
    with _cerrojo_muestras:
        for k in [k for k, (t, _) in _MUESTRAS.items() if ahora - t > _MUESTRAS_TTL]:
            del _MUESTRAS[k]
        while len(_MUESTRAS) >= _MUESTRAS_MAX:
            del _MUESTRAS[min(_MUESTRAS, key=lambda k: _MUESTRAS[k][0])]
        codigo = secrets.token_urlsafe(16)
        _MUESTRAS[codigo] = (ahora, wav)
        return codigo


def muestra(codigo: str, ahora: float | None = None) -> bytes | None:
    ahora = time.monotonic() if ahora is None else ahora
    with _cerrojo_muestras:
        hallada = _MUESTRAS.get(codigo)
        if hallada is None or ahora - hallada[0] > _MUESTRAS_TTL:
            _MUESTRAS.pop(codigo, None)
            return None
        return hallada[1]


def quitar_muestra(codigo: str) -> None:
    with _cerrojo_muestras:
        _MUESTRAS.pop(codigo, None)


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
    return ""  # sin dato: no se muestra la etiqueta


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
                "costo": "Gratis",
            }
        )
    voces.extend(clonadas())
    voces.extend(de_elevenlabs())
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
            "costo": "Gratis",
        }
    )
    return voces, error


def _es_ia(perfil: dict) -> bool:
    return str(perfil.get("personality") or "") == voicestudio.MARCA_IA


def clonadas() -> list[dict]:
    """Las voces de VoiceStudio: las clonadas con consentimiento registrado (sin él no se ofrecen) y las creadas
    con IA, que no son de nadie."""
    if not voicestudio.is_configured():
        return []
    try:
        perfiles = voicestudio.list_profiles()
    except voicestudio.VoiceStudioError as exc:
        log.warning("Voces clonadas: %s", exc)
        return []
    salida = []
    for p in perfiles:
        if not p.get("id") or p.get("kind", "clone") != "clone":
            continue
        ia = _es_ia(p)
        if not ia and not p.get("verified_own_voice"):
            continue
        salida.append(
            {
                "id": voicestudio.id_callbot(str(p["id"])),
                "nombre": _nombre_limpio(str(p.get("name") or "Sin nombre")),
                "genero": _genero(p),
                "tipo": "Creada con IA" if ia else "Clonada",
                "descripcion": "Inventada por el motor de voz: no es de ninguna persona" if ia else (p.get("description") or ""),
                "motor": "omnivoice",
                "motor_nombre": MOTORES["omnivoice"]["nombre"],
                "detalle": "Habla en vivo, sin esperas" if ia else MOTORES["omnivoice"]["detalle"],
                "en_vivo": True,
                "predeterminada": False,
                "escuchar": True,
                "consentimiento": "" if ia else (p.get("consent_text") or ""),
                "creada": p.get("created_at"),
                "costo": "Gratis",
            }
        )
    return salida


_GENERO_ELEVEN = {"female": "Mujer", "male": "Hombre"}


def de_elevenlabs() -> list[dict]:
    """Las voces de la cuenta de ElevenLabs. De pago: se avisa en pantalla y se ofrecen aparte de las gratis."""
    if not elevenlabs.is_configured():
        return []
    try:
        voces_cuenta = elevenlabs.listar_voces()
    except elevenlabs.ElevenLabsError as exc:
        log.warning("Voces de ElevenLabs: %s", exc)
        return []
    salida = []
    for v in voces_cuenta:
        voz_id = str(v.get("voice_id") or "")
        if not voz_id:
            continue
        etiquetas = v.get("labels") or {}
        propia = str(v.get("category") or "") in ("cloned", "professional", "generated")
        detalles = [str(etiquetas[k]) for k in ("accent", "descriptive", "use_case") if etiquetas.get(k)]
        salida.append(
            {
                "id": elevenlabs.id_callbot(voz_id),
                "nombre": str(v.get("name") or "Sin nombre").split(" - ")[0].strip()[:40],
                "genero": _GENERO_ELEVEN.get(str(etiquetas.get("gender") or "").lower(), ""),
                "tipo": "Clonada" if propia else "Prediseñada",
                "descripcion": " · ".join(detalles) or (v.get("description") or "")[:120],
                "motor": "elevenlabs",
                "motor_nombre": MOTORES["elevenlabs"]["nombre"],
                "detalle": MOTORES["elevenlabs"]["detalle"],
                "en_vivo": True,
                "predeterminada": False,
                "escuchar": True,
                "costo": "De pago",
            }
        )
    return salida


def elegida(voces: list[dict], voice_profile_id: str | None) -> dict | None:
    """La voz que usa el agente: la suya, o la predeterminada del servidor si no eligió ninguna."""
    buscada = voice_profile_id or settings.voicebox_profile_id or "piper"
    return next((v for v in voces if v["id"] == buscada), None)
