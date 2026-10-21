"""Cotizador de repuestos (quote.santarosa.lat): lo que el agente de recepción consulta en vivo.

Responde las dos preguntas que más llegan a posventa por repuestos:

- «¿Qué pasó con mi pedido?»: las solicitudes y cotizaciones del cliente, línea por línea, con su disponibilidad
  (disponible, sobre pedido, en análisis, sin solución) y, si se encarga, en cuántos días llega.
- «¿Tienen tal repuesto?»: el inventario importado al Cotizador, con su stock.

El Cotizador no guarda fecha de llegada ni embarques (eso vive en COMEX): solo los días que estimó el analista
(`plazo_dias`, desde que respondió) o el mostrador al cotizar (`dias_estimados_vendedor`). La fecha que sale de
sumarlos es aproximada y se le dice así al cliente.

Se entra con un usuario propio de SOLO LECTURA (`callbot_lectura`) que ve únicamente las columnas que se usan acá,
por la red interna `callbot-cotizador` (docs/datos-en-vivo.md). Nada de esto escribe en el Cotizador. Cualquier
falla (sin conexión, consulta lenta) devuelve un texto que dice que el sistema no respondió: la llamada sigue.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from app.config import settings

log = logging.getLogger(__name__)

TIPOS = ("pedidos", "stock")
MAX_SOLICITUDES = 4
MAX_RESULTADOS_STOCK = 6
DIAS_HACIA_ATRAS = 180
# Palabras que no ayudan a encontrar un repuesto en la descripción del inventario.
VACIAS = {
    "de", "del", "la", "las", "el", "los", "un", "una", "uno", "para", "por", "con", "sin", "que", "mi", "su", "tu",
    "hay", "tienen", "tenes", "tiene", "busco", "quiero", "necesito", "repuesto", "repuestos", "pieza", "piezas",
    "precio", "cuanto", "cuesta", "sale", "stock", "auto", "vehiculo", "camioneta", "original", "originales", "y", "o",
}
DISPONIBILIDAD = {
    "disponible": "disponible (había stock al cotizar)",
    "sobre_pedido": "se encarga (sobre pedido)",
    "en_analisis": "el área de repuestos lo está analizando",
    "sin_solucion": "el área no lo consiguió",
    "no_disponible": "no hay en depósito",
    "para_precio": "esperando precio",
}

_motor: Engine | None = None


class CotizadorNoDisponible(RuntimeError):
    """Sin conexión configurada o la base no respondió."""


def configurado() -> bool:
    return bool(settings.repuestos_database_url.strip())


def _engine() -> Engine:
    global _motor
    if not configurado():
        raise CotizadorNoDisponible("sin REPUESTOS_DATABASE_URL")
    if _motor is None:
        _motor = create_engine(
            settings.repuestos_database_url,
            pool_size=2, max_overflow=1, pool_pre_ping=True, pool_recycle=600,
            # Además de lo que ya fija el usuario en la base: solo lectura y nada que tarde más de 3 s.
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000 -c default_transaction_read_only=on"},
        )
    return _motor


# ─────────────────────────────────────────────────────────── normalizar lo que dice el cliente
def telefono_normal(numero: str | None) -> str | None:
    """Mismo criterio que el Cotizador: solo dígitos, sin 00 ni 595 adelante, sin ceros iniciales, 6 dígitos o más.
    «+595 981 123 456», «0981123456» y «981 123 456» dan los tres «981123456»."""
    digitos = re.sub(r"\D", "", numero or "")
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if digitos.startswith("595") and len(digitos) - 3 >= 8:
        digitos = digitos[3:]
    digitos = digitos.lstrip("0")
    return digitos if len(digitos) >= 6 else None


def telefonos_en(campo: str | None) -> list[str]:
    """Un campo del Cotizador puede traer varios números («0981 111 222 / 021 555 666»)."""
    partes = re.split(r"\s*(?:/|,|;|\||\by\b|\bo\b|\s-\s)\s*", campo or "")
    return [t for t in (telefono_normal(p) for p in partes) if t]


def documento_normal(numero: str | None) -> str | None:
    """CI o RUC, solo dígitos («1.234.567» → «1234567»; «80012345-6» → «800123456»)."""
    digitos = re.sub(r"\D", "", numero or "")
    return digitos if 5 <= len(digitos) <= 12 else None


def _plano(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ]+", " ", sin_tildes)).strip()


def palabras_de_busqueda(texto: str) -> list[str]:
    return list(dict.fromkeys(p for p in _plano(texto).split() if len(p) >= 3 and p not in VACIAS))[:6]


# ─────────────────────────────────────────────────────────── consultas
@dataclass
class Cliente:
    id: int
    nombre: str
    por: str  # «su teléfono» | «su documento»


def buscar_clientes(con: Connection, *, telefono: str | None = None, documento: str | None = None) -> list[Cliente]:
    encontrados: dict[int, Cliente] = {}
    if documento:
        filas = con.execute(text(
            "SELECT id, name, ruc FROM clients WHERE regexp_replace(coalesce(ruc, ''), '\\D', '', 'g') LIKE :doc || '%' "
            "LIMIT 5"), {"doc": documento}).all()
        for f in filas:
            ruc = re.sub(r"\D", "", f.ruc or "")
            # La CI sola o la CI con el dígito verificador del RUC: nunca un número que solo empieza igual.
            if ruc == documento or (len(ruc) == len(documento) + 1 and "-" in (f.ruc or "")):
                encontrados[f.id] = Cliente(f.id, f.name or "", "su documento")
    if telefono and not encontrados:
        filas = con.execute(text(
            "SELECT id, name, phone, mobile FROM clients WHERE regexp_replace(coalesce(mobile, '') || ' ' || "
            "coalesce(phone, ''), '\\D', '', 'g') LIKE '%' || :tel || '%' LIMIT 10"), {"tel": telefono}).all()
        for f in filas:
            if telefono in telefonos_en(f.mobile) + telefonos_en(f.phone):
                encontrados[f.id] = Cliente(f.id, f.name or "", "su teléfono")
    return list(encontrados.values())[:3]


def _fecha(momento: datetime | None) -> str:
    if not momento:
        return "?"
    local = momento.astimezone(ZoneInfo(settings.tz)) if momento.tzinfo else momento
    return local.strftime("%d/%m")


def _llegada(item, cotizada_en: datetime | None) -> str:  # noqa: ANN001 - fila de quote_items
    """«llega en unos 15 días: alrededor del 20/10 (aproximado)», o nada si no hay plazo cargado."""
    if item.plazo_dias:
        base, dias = item.respondido_at or cotizada_en, int(item.plazo_dias)
    elif item.dias_estimados_vendedor:
        base, dias = cotizada_en, int(item.dias_estimados_vendedor)
    else:
        return "sin plazo cargado"
    if dias <= 0:
        return "entrega inmediata según el área"
    if base is None:
        return f"llega en unos {dias} días"
    return f"llega en unos {dias} días desde el {_fecha(base)}: alrededor del {_fecha(base + timedelta(days=dias))} (aproximado)"


def pedidos(con: Connection, clientes: list[Cliente], *, precios: bool = False, ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(timezone.utc)
    ids = [c.id for c in clientes]
    solicitudes = con.execute(text(
        "SELECT r.id, r.request_number, r.status, r.created_at, r.vehicle_description, r.items, s.nombre AS sucursal, "
        "       q.id AS qid, q.quote_number, q.status AS qstatus, q.created_at AS qcreated, q.approved_at "
        "FROM quote_requests r "
        "LEFT JOIN quotes q ON q.source_request_id = r.id AND q.anulada_at IS NULL "
        "LEFT JOIN sucursales s ON s.id = r.sucursal_id "
        "WHERE r.client_id = ANY(:ids) AND r.anulada_at IS NULL AND r.created_at >= :desde "
        "ORDER BY r.created_at DESC LIMIT :max"),
        {"ids": ids, "desde": ahora - timedelta(days=DIAS_HACIA_ATRAS), "max": MAX_SOLICITUDES}).all()
    if not solicitudes:
        return "No tiene solicitudes ni cotizaciones de repuestos en los últimos seis meses."

    qids = [s.qid for s in solicitudes if s.qid]
    items: dict[int, list] = {}
    if qids:
        for it in con.execute(text(
            "SELECT quote_id, description, quantity, disponibilidad, plazo_dias, dias_estimados_vendedor, "
            "respondido_at, decision_cliente, reservado_hasta, unit_price FROM quote_items "
            "WHERE quote_id = ANY(:qids) ORDER BY id"), {"qids": qids}).all():
            items.setdefault(it.quote_id, []).append(it)

    bloques = []
    for s in solicitudes:
        vehiculo = " ".join(p.strip() for p in (s.vehicle_description or "").split("|")[:2] if p.strip())
        cab = f"Solicitud {s.request_number} del {_fecha(s.created_at)}"
        if vehiculo:
            cab += f", para {vehiculo.title()}"
        if s.sucursal:
            cab += f" (sucursal {s.sucursal})"
        if s.status == "Pendiente de cotización" or not s.qid:
            estado = f"todavía la están cotizando ({s.status.lower()})"
        elif s.approved_at:
            estado = f"cotización {s.quote_number}: el cliente ya decidió el {_fecha(s.approved_at)}"
        else:
            estado = f"cotización {s.quote_number} enviada el {_fecha(s.qcreated)}, falta que el cliente decida"
        lineas = [f"{cab}: {estado}."]
        if s.qid:
            for it in items.get(s.qid, []):
                detalle = DISPONIBILIDAD.get(it.disponibilidad or "", it.disponibilidad or "sin dato")
                if it.disponibilidad == "sobre_pedido":
                    detalle += f", {_llegada(it, s.qcreated)}"
                if it.decision_cliente == "aceptado":
                    detalle += "; el cliente lo aceptó"
                elif it.decision_cliente == "descartado":
                    detalle += "; el cliente lo descartó"
                if it.reservado_hasta and it.reservado_hasta > ahora:
                    detalle += f"; reservado hasta el {_fecha(it.reservado_hasta)}"
                if precios and it.unit_price:
                    detalle += f"; precio unitario {_guaranies(it.unit_price)}"
                lineas.append(f"  - {it.description} (x{it.quantity or 1}): {detalle}")
        else:
            for it in (s.items or [])[:8]:
                if isinstance(it, dict) and it.get("description"):
                    lineas.append(f"  - {it['description']} (x{it.get('quantity') or 1}): pedido, todavía sin cotizar")
        bloques.append("\n".join(lineas))
    return "\n".join(bloques)


def _guaranies(valor: float) -> str:
    return "Gs. " + f"{round(valor):,}".replace(",", ".")


def stock(con: Connection, texto: str, *, precios: bool = False) -> str:
    palabras = palabras_de_busqueda(texto)
    if not palabras:
        return "No entendí qué repuesto buscar: pedile al cliente el nombre del repuesto y el modelo del vehículo."
    pajar = ("translate(lower(coalesce(description, '') || ' ' || coalesce(brand, '') || ' ' || coalesce(code, '')), "
             "'áéíóúü', 'aeiouu')")
    puntaje = " + ".join(f"(CASE WHEN {pajar} LIKE :p{i} THEN 1 ELSE 0 END)" for i in range(len(palabras)))
    # Con varias palabras alcanza con que coincidan todas menos una: el modelo del vehículo no siempre está en la
    # descripción del repuesto.
    minimo = max(1, len(palabras) - 1)
    filas = con.execute(text(
        f"SELECT description, brand, code, stock, price, location, updated_at, ({puntaje}) AS puntaje "
        f"FROM inventory WHERE ({puntaje}) >= :minimo ORDER BY puntaje DESC, stock DESC NULLS LAST LIMIT :max"),
        {**{f"p{i}": f"%{p}%" for i, p in enumerate(palabras)}, "minimo": minimo, "max": MAX_RESULTADOS_STOCK}).all()
    if not filas:
        return f"No hay nada parecido a «{' '.join(palabras)}» en el inventario del Cotizador."
    al_dia = max((f.updated_at for f in filas if f.updated_at), default=None)
    lineas = [f"Inventario del Cotizador (cargado el {_fecha(al_dia)}), lo más parecido a «{' '.join(palabras)}»:"]
    for f in filas:
        cuanto = f"{f.stock} en stock" if (f.stock or 0) > 0 else "sin stock"
        dato = f"  - {f.description} (código {f.code}{', ' + f.brand if f.brand else ''}): {cuanto}"
        if precios and f.price and (f.stock or 0) > 0:
            dato += f", precio de lista {_guaranies(f.price)}"
        lineas.append(dato)
    lineas.append("Que esté en el inventario no lo reserva: para apartarlo lo tiene que confirmar el área de repuestos.")
    return "\n".join(lineas)


# ─────────────────────────────────────────────────────────── lo que pide Claude
def consultar(pedido: dict, *, telefono_llamada: str | None = None, precios: bool = False) -> str:
    """Corre la consulta que pidió Claude ({"que": "pedidos", "documento"|"telefono"} o {"que": "stock", "texto"})
    y devuelve el resultado como texto para el siguiente turno. Nunca levanta."""
    que = pedido.get("que")
    try:
        with _engine().connect() as con:
            if que == "stock":
                return stock(con, str(pedido.get("texto") or ""), precios=precios)
            if que == "pedidos":
                documento = documento_normal(str(pedido.get("documento") or ""))
                telefono = telefono_normal(str(pedido.get("telefono") or "")) or telefono_normal(telefono_llamada)
                if not documento and not telefono:
                    return "Para buscar sus pedidos hace falta su número de cédula (o RUC) o el teléfono con que lo registraron."
                clientes = buscar_clientes(con, telefono=telefono, documento=documento)
                if not clientes:
                    por = "ese documento" if documento else "ese teléfono"
                    return f"No hay ningún cliente con {por} en el Cotizador. Puede estar registrado con otro dato."
                nombres = ", ".join(c.nombre for c in clientes if c.nombre)
                return f"Cliente encontrado por {clientes[0].por}: {nombres}.\n" + pedidos(con, clientes, precios=precios)
            return "Consulta desconocida."
    except CotizadorNoDisponible:
        return "El sistema de repuestos no está conectado: no se puede consultar ahora."
    except Exception as exc:  # noqa: BLE001 - del otro lado hay alguien esperando: nunca se corta por esto
        log.warning("Cotizador: la consulta %s falló: %s", que, exc.__class__.__name__)
        return "El sistema de repuestos no respondió a tiempo: no se pudo consultar."


def estado() -> dict:
    """Para la ficha del agente: si hay conexión y cuántos ítems y solicitudes se ven."""
    if not configurado():
        return {"conectado": False, "motivo": "Falta configurar la conexión al Cotizador en el servidor."}
    try:
        with _engine().connect() as con:
            fila = con.execute(text(
                "SELECT (SELECT count(*) FROM inventory) AS inventario, (SELECT count(*) FROM quote_requests) AS solicitudes, "
                "(SELECT max(updated_at) FROM inventory) AS al_dia")).one()
        return {"conectado": True, "inventario": fila.inventario, "solicitudes": fila.solicitudes,
                "al_dia": _fecha(fila.al_dia)}
    except Exception as exc:  # noqa: BLE001
        log.warning("Cotizador: sin conexión (%s)", exc.__class__.__name__)
        return {"conectado": False, "motivo": "El Cotizador no responde desde Callbot."}
