"""Sistemas que el agente de recepción consulta en vivo: el Cotizador de repuestos y la agenda del taller.

Cada agente elige en su ficha qué consulta (`Campaign.fuentes`): {"repuestos": bool, "precios": bool, "turnos": bool}.
Lo que no está disponible no se ofrece aunque esté marcado: el Cotizador sin conexión configurada, o la agenda sin un
taller activo con horario.

Las consultas al Cotizador van a otra base y corren en un hilo. Las de la agenda usan una sesión propia que hace
commit enseguida: así la reserva no queda bloqueando la sucursal mientras Claude arma la respuesta.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import update
from sqlalchemy.orm import Session, object_session

from app import agenda
from app.db import session_scope
from app.fuentes import cotizador
from app.models import TallerTurno


def elegidas(campaign) -> dict[str, bool]:  # noqa: ANN001 - Campaign
    f = getattr(campaign, "fuentes", None) or {}
    return {"repuestos": bool(f.get("repuestos")), "precios": bool(f.get("precios")), "turnos": bool(f.get("turnos"))}


def vocabulario(db: Session, campaign) -> list[str]:  # noqa: ANN001 - Campaign
    """Nombres que el agente tiene que reconocer de oído por los sistemas que consulta: los talleres de la agenda
    («Taller Zeballos»; sin esto Whisper escribía «Ceballos»)."""
    if not elegidas(campaign)["turnos"]:
        return []
    return [s.nombre for s in agenda.sucursales_activas(db)]


def repuestos_activos(campaign) -> bool:  # noqa: ANN001
    return elegidas(campaign)["repuestos"] and cotizador.configurado()


def bloque(caso, db: Session | None = None) -> str:  # noqa: ANN001 - Caso
    """Lo que se le explica a Claude sobre las consultas que puede pedir en este caso ("" si ninguna)."""
    from app import recepcion

    partes = []
    if repuestos_activos(caso.campaign):
        partes.append(recepcion.texto_repuestos(telefono_conocido=bool(caso.telefono),
                                                precios=elegidas(caso.campaign)["precios"]))
    if elegidas(caso.campaign)["turnos"]:
        sesion = db or object_session(caso)
        if sesion is not None:
            partes.append(agenda.texto_para_claude(sesion))
        else:
            with session_scope() as nueva:
                partes.append(agenda.texto_para_claude(nueva))
    return recepcion.bloque_consultas(partes)


def _agenda(pedido: dict, telefono: str | None, origen: str, cliente: str | None) -> tuple[str, int | None]:
    with session_scope() as db:
        texto, turno_id = agenda.consultar(db, pedido, telefono_llamada=telefono, origen=origen, cliente=cliente)
    if turno_id:
        from app import avisos

        avisos.por_turno_reservado(turno_id)  # ya con commit; un turno de prueba no avisa a nadie
    return texto, turno_id


def consultor(caso) -> Callable[[dict], Awaitable[str]]:  # noqa: ANN001
    """La función que corre lo que pidió Claude. Una reserva queda atada al caso (y el caso, al turno)."""
    from app import recepcion

    precios = elegidas(caso.campaign)["precios"]

    async def consultar(pedido: dict) -> str:
        if pedido["que"] in ("pedidos", "stock"):
            return await asyncio.to_thread(cotizador.consultar, pedido, telefono_llamada=caso.telefono, precios=precios)
        texto, turno_id = await asyncio.to_thread(_agenda, pedido, caso.telefono, caso.origen, caso.nombre)
        if turno_id:
            recepcion.evento(caso, "turno reservado", f"Turno n.º {turno_id}")
            caso.datos = {**(caso.datos or {}), "turno": str(turno_id)}
            sesion = object_session(caso)
            if sesion is not None and caso.id:
                sesion.execute(update(TallerTurno).where(TallerTurno.id == turno_id).values(caso_id=caso.id))
        return texto

    return consultar
