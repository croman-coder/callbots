"""Sistemas que el agente de recepción consulta en vivo (por ahora, el Cotizador de repuestos).

Cada agente elige en su ficha qué consulta (`Campaign.fuentes`): {"repuestos": bool, "precios": bool}. Sin la conexión
configurada en el servidor no se ofrece nada, aunque el agente lo tenga marcado.
"""

from __future__ import annotations

from collections.abc import Callable

from app.fuentes import cotizador


def elegidas(campaign) -> dict[str, bool]:  # noqa: ANN001 - Campaign
    f = getattr(campaign, "fuentes", None) or {}
    return {"repuestos": bool(f.get("repuestos")), "precios": bool(f.get("precios"))}


def repuestos_activos(campaign) -> bool:  # noqa: ANN001
    return elegidas(campaign)["repuestos"] and cotizador.configurado()


def bloque(caso) -> str:  # noqa: ANN001 - Caso
    """Lo que se le explica a Claude sobre las consultas que puede pedir en este caso ("" si ninguna)."""
    from app import recepcion

    if not repuestos_activos(caso.campaign):
        return ""
    return recepcion.bloque_consultas(telefono_conocido=bool(caso.telefono), precios=elegidas(caso.campaign)["precios"])


def consultor(caso) -> Callable[[dict], str]:  # noqa: ANN001
    precios = elegidas(caso.campaign)["precios"]
    return lambda pedido: cotizador.consultar(pedido, telefono_llamada=caso.telefono, precios=precios)
