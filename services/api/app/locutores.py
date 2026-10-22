"""Voz de locutor a distancia: un enlace para que la persona grabe su permiso y varias tomas desde su celular.

Pedido de la dirección regional de posventa (21/10/2026): una voz local, paraguaya, no la de un colombiano. La forma
de tenerla es clonar la voz de un locutor de acá, y un locutor no va a venir a la oficina a grabar frente al panel.
Entonces:

1. Desde Voces se crea un enlace para esa persona (de un solo uso, vence en DIAS_VIGENCIA días).
2. La persona lo abre en su celular, graba su permiso (la misma frase que en el panel) y cinco frases cortas de
   posventa, con nombres de lugares de acá: así el clon aprende la tonada y cómo dice «Mariano Roque Alonso».
3. En Voces se escucha el permiso y cada toma, se elige la mejor y se aprueba: recién ahí se clona en VoiceStudio,
   con el permiso grabado y el registro de quién lo aprobó. Lo rechazado se borra.

Cada toma dura como mucho MAX_TOMA_SEGUNDOS: la placa de video del servidor solo alcanza para referencias cortas
(ver docs/voces-clonadas-gpu.md). Los audios quedan en RECORDINGS_DIR/locutores/<id>/ hasta que se aprueban (el
permiso y la toma elegida pasan a VoiceStudio) o se rechazan.
"""

from __future__ import annotations

import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import voces
from app.config import settings
from app.models import GrabacionVoz
from app.services import voicestudio

DIAS_VIGENCIA = 7
MAX_TOMA_SEGUNDOS = voces.MAX_REFERENCIA_SEGUNDOS
MIN_TOMA_SEGUNDOS = 2.0
MAX_PERMISO_SEGUNDOS = 20.0
MAX_BYTES = 3 * 1024 * 1024
ESTADOS = {"esperando": "Esperando la grabación", "recibida": "Para revisar", "aprobada": "Aprobada",
           "rechazada": "Rechazada"}


class GrabacionInvalida(ValueError):
    """Lo que vino no sirve. El mensaje se le puede mostrar a quien graba."""


def frases(genero: str) -> list[str]:
    """Lo que lee el locutor: frases reales de la recepción de posventa, cortas (unos 5 segundos cada una) y con
    nombres de acá, para que el clon aprenda la tonada y cómo se dicen."""
    articulo = "la" if genero == "Mujer" else "el"
    return [
        f"Hola, buenas tardes. Le habla {articulo} asistente de Santa Rosa, ¿en qué lo puedo ayudar?",
        "Perfecto, ya le busco un turno para el service de su vehículo.",
        "Su repuesto llega aproximadamente el quince de diciembre.",
        "Le paso su caso a Repuestos: le responden en las próximas veinticuatro horas.",
        "Tenemos talleres en Asunción, Mariano Roque Alonso y Ciudad del Este.",
    ]


def _ahora(ahora: datetime | None) -> datetime:
    return ahora or datetime.now(timezone.utc)


def _utc(momento: datetime) -> datetime:
    return momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)


def nuevo(db: Session, persona: str, genero: str, nota: str | None, por: str | None,
          ahora: datetime | None = None) -> GrabacionVoz:
    persona = " ".join((persona or "").split())[:80]
    if not persona:
        raise GrabacionInvalida("Poné el nombre y apellido de quien va a grabar.")
    if genero not in ("Mujer", "Hombre"):
        raise GrabacionInvalida("Elegí si es voz de mujer o de hombre.")
    g = GrabacionVoz(token=secrets.token_urlsafe(24), persona=persona, genero=genero,
                     nota=" ".join((nota or "").split())[:300] or None, creada_por=por, estado="esperando",
                     expira_at=_ahora(ahora) + timedelta(days=DIAS_VIGENCIA))
    db.add(g)
    db.flush()
    return g


def vigente(g: GrabacionVoz, ahora: datetime | None = None) -> bool:
    return g.estado == "esperando" and _utc(g.expira_at) > _ahora(ahora)


def enlace(g: GrabacionVoz) -> str:
    return f"{settings.panel_url.rstrip('/')}/grabar/{g.token}"


def carpeta(g: GrabacionVoz) -> Path:
    return Path(settings.recordings_dir) / "locutores" / str(g.id)


def _es_wav(datos: bytes) -> bool:
    return len(datos) > 44 and datos[:4] == b"RIFF" and datos[8:12] == b"WAVE"


def _revisar(datos: bytes | None, minimo: float, maximo: float, que: str) -> float:
    if not datos:
        raise GrabacionInvalida(f"Falta {que}.")
    if len(datos) > MAX_BYTES or not _es_wav(datos):
        raise GrabacionInvalida(f"{que.capitalize()} no se pudo leer. Grabala de nuevo desde esta página.")
    segundos = voicestudio.duracion_wav(datos)
    if segundos is None:
        raise GrabacionInvalida(f"{que.capitalize()} no se pudo leer. Grabala de nuevo desde esta página.")
    if segundos < minimo:
        raise GrabacionInvalida(f"{que.capitalize()} quedó muy corta: grabala de nuevo leyendo el texto completo.")
    if segundos > maximo + 0.5:
        raise GrabacionInvalida(f"{que.capitalize()} dura {segundos:.0f} segundos; tiene que durar como mucho {maximo:.0f}.")
    return round(segundos, 1)


def recibir(g: GrabacionVoz, consentimiento: bytes | None, tomas: list[bytes | None],
            ahora: datetime | None = None) -> None:
    """Valida y guarda lo que grabó la persona. Levanta GrabacionInvalida (con un mensaje para ella)."""
    if not vigente(g, ahora):
        raise GrabacionInvalida("Este enlace ya no sirve: venció o ya se usó.")
    textos = frases(g.genero)
    if len(tomas) != len(textos):
        raise GrabacionInvalida("Faltan frases por grabar.")
    _revisar(consentimiento, 3.0, MAX_PERMISO_SEGUNDOS, "la grabación del permiso")
    duraciones = [_revisar(t, MIN_TOMA_SEGUNDOS, MAX_TOMA_SEGUNDOS, f"la frase {i}") for i, t in enumerate(tomas, 1)]

    destino = carpeta(g)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "consentimiento.wav").write_bytes(consentimiento)
    registro = []
    for i, (audio, frase, segundos) in enumerate(zip(tomas, textos, duraciones), start=1):
        nombre = f"toma-{i}.wav"
        (destino / nombre).write_bytes(audio)
        registro.append({"archivo": nombre, "frase": frase, "segundos": segundos})
    fecha = _ahora(ahora).astimezone(ZoneInfo("America/Asuncion")).strftime("%d/%m/%Y %H:%M")
    g.tomas = registro
    g.consentimiento_texto = (f"{voces.frase_consentimiento(g.persona)} Grabado a distancia, con el enlace personal "
                              f"de Callbot, el {fecha} (hora de Paraguay).")
    g.estado = "recibida"
    g.recibida_at = _ahora(ahora)


def audio(g: GrabacionVoz, archivo: str) -> bytes | None:
    """Un audio de la grabación, solo los que se guardaron (nada de rutas armadas desde afuera)."""
    permitidos = {"consentimiento.wav", *(t["archivo"] for t in g.tomas or [])}
    if archivo not in permitidos:
        return None
    ruta = carpeta(g) / archivo
    return ruta.read_bytes() if ruta.is_file() else None


def aprobar(g: GrabacionVoz, indice: int, nombre: str, por: str | None, ahora: datetime | None = None) -> str:
    """Clona la voz con la toma elegida y el permiso grabado. Devuelve el id de la voz para los agentes."""
    if g.estado != "recibida":
        raise GrabacionInvalida("Esta grabación no está para revisar.")
    tomas = g.tomas or []
    if not 0 <= indice < len(tomas):
        raise GrabacionInvalida("Elegí una de las tomas.")
    nombre = " ".join((nombre or "").split())[:40] or g.persona
    toma = tomas[indice]
    referencia = audio(g, toma["archivo"])
    permiso = audio(g, "consentimiento.wav")
    if referencia is None or permiso is None:
        raise GrabacionInvalida("Faltan los audios de esta grabación en el servidor.")
    perfil = voicestudio.clonar(
        f"{nombre} ({g.genero.lower()})", referencia, toma["archivo"], toma["frase"], permiso, "consentimiento.wav",
        f"{g.consentimiento_texto} Revisado y aprobado por {por or 'el equipo'}.",
    )
    g.perfil_id = voicestudio.id_callbot(str(perfil["id"]))
    g.estado = "aprobada"
    g.revisada_por, g.revisada_at = por, _ahora(ahora)
    return g.perfil_id


def rechazar(g: GrabacionVoz, motivo: str, por: str | None, ahora: datetime | None = None) -> None:
    g.estado = "rechazada"
    g.motivo = " ".join((motivo or "").split())[:300] or None
    g.revisada_por, g.revisada_at = por, _ahora(ahora)
    borrar_archivos(g)


def borrar_archivos(g: GrabacionVoz) -> None:
    shutil.rmtree(carpeta(g), ignore_errors=True)
