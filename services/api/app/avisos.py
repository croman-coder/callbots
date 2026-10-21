"""Avisos por WhatsApp (YCloud): turno confirmado, recordatorio, vehículo listo y caso nuevo para el área.

WhatsApp solo deja que la empresa escriba primero con **plantillas aprobadas por Meta**. Un texto libre fuera de las
24 h desde el último mensaje del cliente no rebota en el momento: YCloud lo acepta con 200 y lo da por fallido
después, por el webhook `whatsapp.message.updated` (código 131047). Botika lo midió en producción el 12/09/2026:
el 31 % de los mensajes de asesores no llegó y figuraban como enviados. Por eso acá:

- todos los avisos son plantillas (categoría «utilidad»), con el texto sugerido en EVENTOS;
- cada envío guarda el id que devuelve YCloud, y el webhook (firmado) lo mueve a entregado, leído o falló;
- sin clave o sin número no sale nada: el aviso queda registrado como «sin configurar», así se ve qué se habría
  mandado.

El número tiene que ser propio de posventa, no el de Botika (ventas): si el cliente responde, le contestaría el bot
de ventas.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import agenda
from app.config import settings
from app.db import session_scope
from app.fuentes.cotizador import telefono_normal
from app.models import AvisoConfig, Caso, TallerTurno, WhatsappEnvio

log = logging.getLogger(__name__)

API = "https://api.ycloud.com/v2"
TIMEOUT = 15.0
# Firma del webhook: más vieja que esto se descarta (evita que reenvíen un evento capturado).
TOLERANCIA_FIRMA = 300


@dataclass(frozen=True)
class Evento:
    titulo: str
    a_quien: str
    cuando: str
    plantilla: str  # nombre sugerido para pedir la aprobación
    texto: str  # cuerpo de la plantilla, con {{1}}, {{2}}…
    ejemplo: tuple[str, ...]
    hora: str | None = None


EVENTOS: dict[str, Evento] = {
    "turno_confirmado": Evento(
        "Turno confirmado", "Cliente", "Apenas se reserva el turno (por el agente o a mano)",
        "callbot_turno_confirmado",
        "Hola {{1}}, su turno en {{2}} quedó agendado para el {{3}}. Servicio: {{4}}. Puede retirar el vehículo "
        "{{5}}, aproximadamente. Si necesita cambiarlo, llámenos.",
        ("Laura", "Taller Zeballos", "lunes 26/10 a las 14:00", "Service 10.000 km", "desde las 17:00"),
    ),
    "turno_recordatorio": Evento(
        "Recordatorio del turno", "Cliente", "El día anterior al turno, a la hora que se elija",
        "callbot_turno_recordatorio",
        "Hola {{1}}, le recordamos su turno de mañana en {{2}}: {{3}}. Lo esperamos.",
        ("Laura", "Taller Zeballos", "lunes 26/10 a las 14:00"), hora="17:00",
    ),
    "vehiculo_listo": Evento(
        "Vehículo listo", "Cliente", "Cuando el turno pasa a «Listo para retirar» en la agenda",
        "callbot_vehiculo_listo",
        "Hola {{1}}, su {{2}} ya está listo para retirar en {{3}}. Muchas gracias por confiar en nosotros.",
        ("Laura", "Jetour X70", "Taller Zeballos"),
    ),
    "caso_derivado": Evento(
        "Caso nuevo para el área", "Responsable del área", "Cuando el agente de recepción le pasa un caso a su área",
        "callbot_caso_derivado",
        "Nuevo caso para {{1}}: {{2}}. Cliente: {{3}}. Hay que responderle antes del {{4}}. Detalle: {{5}}",
        ("Repuestos", "Espejo encargado hace tres semanas, sin novedades", "Ramón Giménez", "22/10 a las 15:00",
         "https://callbot.santarosa.lat/casos/12"),
    ),
}
ESTADOS = {
    "pendiente": "Pendiente", "enviado": "Enviado", "entregado": "Entregado", "leido": "Leído", "fallo": "Falló",
    "sin_configurar": "Sin configurar", "apagado": "Aviso apagado", "sin_telefono": "Sin teléfono",
}
# Un webhook atrasado no puede volver a «enviado» un mensaje ya leído.
ORDEN = {"pendiente": 0, "enviado": 1, "entregado": 2, "leido": 3, "fallo": 4}
YCLOUD_A_ESTADO = {"sent": "enviado", "delivered": "entregado", "read": "leido", "failed": "fallo"}


class AvisoError(RuntimeError):
    """YCloud no aceptó el pedido. El mensaje se puede mostrar."""


def configurado() -> bool:
    return bool(settings.ycloud_api_key.strip() and e164(settings.ycloud_numero))


def e164(telefono: str | None) -> str | None:
    """«0981 123 456» → «+595981123456». Paraguay salvo que ya traiga otro código de país con +."""
    crudo = (telefono or "").strip()
    if crudo.startswith("+") and not crudo.startswith("+595"):
        digitos = "".join(c for c in crudo if c.isdigit())
        return f"+{digitos}" if 8 <= len(digitos) <= 15 else None
    local = telefono_normal(crudo)
    return f"+595{local}" if local and 8 <= len(local) <= 10 else None


def texto_con(evento: str, variables: list[str] | tuple[str, ...]) -> str:
    """Cómo se lee el aviso con estos datos (para la vista previa y el registro)."""
    texto = EVENTOS[evento].texto
    for i, valor in enumerate(variables, start=1):
        texto = texto.replace("{{%d}}" % i, valor)
    return texto


# ─────────────────────────────────────────────────────────── configuración
def config(db: Session, evento: str) -> AvisoConfig:
    fila = db.get(AvisoConfig, evento)
    if fila is None:
        fila = AvisoConfig(evento=evento, activo=False, plantilla=EVENTOS[evento].plantilla, idioma="es",
                           hora=EVENTOS[evento].hora)
        db.add(fila)
        db.flush()
    return fila


def todas(db: Session) -> dict[str, AvisoConfig]:
    return {e: config(db, e) for e in EVENTOS}


# ─────────────────────────────────────────────────────────── registrar y mandar
def registrar(db: Session, evento: str, *, telefono: str | None, nombre: str | None, variables: list[str],
              turno_id: int | None = None, caso_id: int | None = None, prueba: bool = False) -> WhatsappEnvio:
    """Deja el aviso anotado con lo que va a pasar con él. Solo «pendiente» se manda (ver `despachar`)."""
    cfg = config(db, evento)
    destino = e164(telefono)
    if not cfg.activo and not prueba:
        estado = "apagado"
    elif not configurado():
        estado = "sin_configurar"
    elif destino is None:
        estado = "sin_telefono"
    else:
        estado = "pendiente"
    envio = WhatsappEnvio(evento=evento, destino=destino, nombre=(nombre or None) and nombre[:160],
                          plantilla=cfg.plantilla, idioma=cfg.idioma, variables=[v[:300] for v in variables],
                          estado=estado, turno_id=turno_id, caso_id=caso_id, prueba=prueba)
    db.add(envio)
    db.flush()
    return envio


def despachar(envio_id: int) -> None:
    """Encola el envío (lo hace el worker, con reintentos). Se llama después del commit."""
    from app.scheduler.tasks import enviar_aviso

    try:
        enviar_aviso.delay(envio_id)
    except Exception as exc:  # noqa: BLE001 - sin cola el aviso queda pendiente, no se cae la pantalla
        log.error("No se pudo encolar el aviso %s: %s", envio_id, exc)


def _cuerpo(envio: WhatsappEnvio) -> dict:
    return {
        "from": e164(settings.ycloud_numero),
        "to": envio.destino,
        "type": "template",
        "template": {
            "name": envio.plantilla,
            "language": {"code": envio.idioma or "es"},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": v} for v in envio.variables or []]}],
        },
        "externalId": f"callbot-{envio.id}",
    }


def enviar(envio: WhatsappEnvio, cliente: httpx.Client | None = None) -> None:
    """Manda la plantilla. Deja el envío en «enviado» con el id de YCloud o en «fallo» con el motivo. Levanta
    httpx.TransportError si no hubo conexión (el worker reintenta)."""
    if envio.estado != "pendiente":
        return
    propio = cliente is None
    cliente = cliente or httpx.Client(timeout=TIMEOUT)
    try:
        r = cliente.post(f"{API}/whatsapp/messages", json=_cuerpo(envio),
                         headers={"X-API-Key": settings.ycloud_api_key, "Content-Type": "application/json"})
    finally:
        if propio:
            cliente.close()
    if r.status_code >= 300:
        envio.estado = "fallo"
        envio.error = f"YCloud respondió {r.status_code}: {r.text[:300]}"
        log.warning("Aviso %s: %s", envio.id, envio.error)
        return
    datos = r.json() if r.content else {}
    envio.ycloud_id = str(datos.get("id") or "")[:80] or None
    envio.estado = "fallo" if datos.get("status") == "failed" else "enviado"
    envio.error = (datos.get("errorMessage") or None) and str(datos["errorMessage"])[:400]
    envio.enviado_at = datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────── webhook de estados
def firma_valida(cabecera: str | None, cuerpo: bytes, secreto: str, ahora: float | None = None) -> bool:
    """`YCloud-Signature: t=<segundos>,s=<hex>` con s = HMAC-SHA256(secreto, "<t>.<cuerpo>")."""
    if not cabecera or not secreto:
        return False
    partes = dict(p.split("=", 1) for p in cabecera.split(",") if "=" in p)
    t, firma = partes.get("t", ""), partes.get("s", "")
    if not t.isdigit() or abs((ahora or time.time()) - int(t)) > TOLERANCIA_FIRMA:
        return False
    esperada = hmac.new(secreto.encode(), f"{t}.".encode() + cuerpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperada, firma)


def aplicar_estado(db: Session, evento: dict) -> bool:
    """Un `whatsapp.message.updated` de YCloud: mueve el envío hacia adelante (enviado → entregado → leído, o
    falló con el motivo). Devuelve False si no es un envío nuestro."""
    if evento.get("type") != "whatsapp.message.updated":
        return False
    mensaje = evento.get("whatsappMessage") or {}
    envio = db.scalar(select(WhatsappEnvio).where(WhatsappEnvio.ycloud_id == str(mensaje.get("id") or "")))
    if envio is None:
        return False
    nuevo = YCLOUD_A_ESTADO.get(str(mensaje.get("status") or ""))
    if nuevo is None or ORDEN.get(nuevo, 0) < ORDEN.get(envio.estado, 0):
        return True
    envio.estado = nuevo
    if nuevo == "fallo":
        codigo, motivo = mensaje.get("errorCode"), mensaje.get("errorMessage")
        envio.error = (f"{codigo}: " if codigo else "") + str(motivo or "WhatsApp no lo entregó")[:380]
    return True


# ─────────────────────────────────────────────────────────── plantillas en Meta
def _api(metodo: str, ruta: str, **kw) -> dict:
    if not settings.ycloud_api_key.strip():
        raise AvisoError("Falta la clave de YCloud.")
    try:
        r = httpx.request(metodo, f"{API}{ruta}", headers={"X-API-Key": settings.ycloud_api_key}, timeout=TIMEOUT, **kw)
    except httpx.HTTPError as exc:
        raise AvisoError(f"YCloud no respondió ({exc.__class__.__name__}).") from exc
    if r.status_code >= 300:
        raise AvisoError(f"YCloud respondió {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def plantillas_en_meta() -> dict[str, dict]:
    """{nombre: {"estado": APPROVED|PENDING|REJECTED…, "motivo": …}} de la cuenta de WhatsApp de posventa."""
    if not settings.ycloud_waba_id.strip():
        raise AvisoError("Falta el WABA ID de la cuenta de WhatsApp de posventa.")
    datos = _api("GET", "/whatsapp/templates", params={"filter.wabaId": settings.ycloud_waba_id, "limit": 100})
    return {p.get("name"): {"estado": p.get("status"), "motivo": p.get("reason"), "idioma": p.get("language")}
            for p in datos.get("items", []) if p.get("name")}


def pedir_aprobacion(evento: str) -> None:
    """Le manda a Meta la plantilla sugerida del evento (categoría «utilidad») para que la apruebe."""
    e = EVENTOS[evento]
    _api("POST", "/whatsapp/templates", json={
        "wabaId": settings.ycloud_waba_id, "name": e.plantilla, "language": "es", "category": "UTILITY",
        "components": [{"type": "BODY", "text": e.texto, "example": {"body_text": [list(e.ejemplo)]}}],
    })


# ─────────────────────────────────────────────────────────── los eventos
def _primer_nombre(nombre: str | None) -> str:
    return (nombre or "").strip().split(" ")[0].capitalize() or "cliente"


def _taller(turno: TallerTurno) -> str:
    return turno.sucursal.nombre + (f" ({turno.sucursal.direccion})" if turno.sucursal.direccion else "")


def _cuando(turno: TallerTurno) -> str:
    return f"{agenda.dia_texto(agenda.local(turno.inicio).date())} a las {agenda.hora_texto(turno.inicio)}"


def _por_turno(turno_id: int, evento: str) -> int | None:
    with session_scope() as db:
        turno = db.get(TallerTurno, turno_id)
        if turno is None or turno.estado == "prueba":
            return None
        if evento == "turno_confirmado":
            variables = [_primer_nombre(turno.cliente_nombre), _taller(turno), _cuando(turno), turno.servicio_nombre,
                         agenda.retiro_texto(turno.retiro_estimado, turno.inicio) if turno.retiro_estimado else "el mismo día"]
        elif evento == "turno_recordatorio":
            variables = [_primer_nombre(turno.cliente_nombre), _taller(turno), _cuando(turno)]
        else:
            vehiculo = " ".join(p for p in (turno.marca, turno.modelo) if p) or "vehículo"
            variables = [_primer_nombre(turno.cliente_nombre), vehiculo, _taller(turno)]
        envio = registrar(db, evento, telefono=turno.telefono, nombre=turno.cliente_nombre, variables=variables,
                          turno_id=turno.id, caso_id=turno.caso_id)
        envio_id, pendiente = envio.id, envio.estado == "pendiente"
    if pendiente:
        despachar(envio_id)
    return envio_id


def por_turno_reservado(turno_id: int) -> int | None:
    return _por_turno(turno_id, "turno_confirmado")


def por_vehiculo_listo(turno_id: int) -> int | None:
    return _por_turno(turno_id, "vehiculo_listo")


def por_caso_derivado(caso_id: int) -> int | None:
    """Al responsable del área, si su área tiene WhatsApp cargado. Las pruebas no avisan a nadie."""
    from app import recepcion

    with session_scope() as db:
        caso = db.get(Caso, caso_id)
        if caso is None or caso.origen in recepcion.DE_PRUEBA or caso.estado != "abierto":
            return None
        area = recepcion.area_por_clave(caso.campaign, caso.area) or {}
        if not area.get("whatsapp"):
            return None
        vence = agenda.local(caso.vence_at).strftime("%d/%m a las %H:%M") if caso.vence_at else "lo antes posible"
        variables = [caso.area_nombre or "el área", (caso.resumen or "sin resumen")[:200], caso.nombre or caso.telefono or "sin nombre",
                     vence, f"{settings.panel_url.rstrip('/')}/casos/{caso.id}"]
        envio = registrar(db, "caso_derivado", telefono=area["whatsapp"], nombre=area.get("responsable"),
                          variables=variables, caso_id=caso.id)
        envio_id, pendiente = envio.id, envio.estado == "pendiente"
    if pendiente:
        despachar(envio_id)
    return envio_id


def recordatorios(momento: datetime | None = None) -> int:
    """Los turnos de mañana que todavía no tienen recordatorio, si ya pasó la hora elegida. Devuelve cuántos."""
    momento = momento or datetime.now(timezone.utc)
    hoy_local = agenda.local(momento)
    with session_scope() as db:
        cfg = config(db, "turno_recordatorio")
        hora = agenda._hm(cfg.hora or "17:00")
        if not cfg.activo or hora is None or hoy_local.time() < hora:
            return 0
        manana = hoy_local.date() + timedelta(days=1)
        ini = datetime.combine(manana, datetime.min.time(), tzinfo=agenda.tz()).astimezone(timezone.utc)
        turnos = db.scalars(select(TallerTurno.id).where(
            TallerTurno.inicio >= ini, TallerTurno.inicio < ini + timedelta(days=1),
            TallerTurno.estado.in_(("agendado", "confirmado")),
        )).all()
        ya = set(db.scalars(select(WhatsappEnvio.turno_id).where(
            WhatsappEnvio.evento == "turno_recordatorio", WhatsappEnvio.turno_id.in_(turnos or [-1]))).all())
    hechos = 0
    for turno_id in turnos:
        if turno_id not in ya and _por_turno(turno_id, "turno_recordatorio"):
            hechos += 1
    return hechos
