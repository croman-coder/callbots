"""Datos en vivo del Cotizador: normalizar lo que dice el cliente, el plazo aproximado y el turno que consulta."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import fuentes, recepcion
from app.config import settings
from app.fuentes import cotizador
from app.models import Campaign
from app.services import llm

AHORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("entrada,salida", [
    ("+595 981 123 456", "981123456"), ("0981123456", "981123456"), ("981-123-456", "981123456"),
    ("00595981123456", "981123456"), ("021 555 666", "21555666"), ("12345", None), ("", None),
])
def test_telefono_como_lo_normaliza_el_cotizador(entrada, salida):
    assert cotizador.telefono_normal(entrada) == salida


def test_un_campo_con_varios_telefonos():
    assert cotizador.telefonos_en("0981 111 222 / 021 555 666") == ["981111222", "21555666"]


def test_documento_y_palabras_de_busqueda():
    assert cotizador.documento_normal("1.234.567") == "1234567"
    assert cotizador.documento_normal("80012345-6") == "800123456"
    assert cotizador.documento_normal("12") is None
    assert cotizador.palabras_de_busqueda("¿Tienen pastillas de freno para la Duster?") == ["pastillas", "freno", "duster"]


def test_plazo_aproximado_desde_que_respondio_el_area():
    item = SimpleNamespace(plazo_dias=15, dias_estimados_vendedor=None, respondido_at=datetime(2026, 10, 5, 15, tzinfo=timezone.utc))
    assert cotizador._llegada(item, None) == "llega en unos 15 días desde el 05/10: alrededor del 20/10 (aproximado)"
    sin = SimpleNamespace(plazo_dias=None, dias_estimados_vendedor=None, respondido_at=None)
    assert cotizador._llegada(sin, None) == "sin plazo cargado"
    vendedor = SimpleNamespace(plazo_dias=None, dias_estimados_vendedor=7, respondido_at=None)
    assert "alrededor del 12/10" in cotizador._llegada(vendedor, datetime(2026, 10, 5, 15, tzinfo=timezone.utc))


def test_sin_conexion_configurada_no_rompe(monkeypatch):
    monkeypatch.setattr(settings, "repuestos_database_url", "")
    assert "no está conectado" in cotizador.consultar({"que": "stock", "texto": "filtro"})
    assert cotizador.estado()["conectado"] is False


def test_pedidos_sin_dato_para_buscar_pide_la_cedula(monkeypatch):
    class Con:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(cotizador, "_engine", lambda: SimpleNamespace(connect=lambda: Con()))
    assert "cédula" in cotizador.consultar({"que": "pedidos"})


def test_el_interruptor_del_agente_y_la_conexion(monkeypatch):
    c = Campaign(name="R", modo="recepcion", fuentes={"repuestos": True})
    monkeypatch.setattr(cotizador, "configurado", lambda: True)
    assert fuentes.repuestos_activos(c)
    monkeypatch.setattr(cotizador, "configurado", lambda: False)
    assert not fuentes.repuestos_activos(c)  # marcado, pero sin conexión en el servidor no se ofrece
    assert not fuentes.repuestos_activos(Campaign(name="R", modo="recepcion"))


def test_consultar_y_contestar_con_el_dato(monkeypatch):
    pedidos_a_claude: list[tuple[str, str]] = []
    respuestas = [
        {"decir": "Ya lo busco en el sistema, un momento.", "accion": "seguir", "nombre": "Ramón",
         "consultar": {"que": "pedidos", "documento": "1234567"}},
        {"decir": "Su espejo se encargó y llega aproximadamente el 20 de octubre. ¿Algo más?", "accion": "seguir",
         "resumen": "Consulta por espejo encargado"},
    ]

    async def complete(system, user, *, max_tokens=200):
        pedidos_a_claude.append((system, user))
        return json.dumps(respuestas.pop(0), ensure_ascii=False)

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    c = Campaign(id=3, name="Recepción", modo="recepcion", areas=[], fuentes={"repuestos": True})
    caso = recepcion.nuevo(c, "chat", ahora=AHORA)
    recepcion.decir(caso, "agente", "Posventa, ¿en qué lo ayudo?", AHORA)

    bloque = recepcion.bloque_consultas()
    r = asyncio.run(recepcion.turno(caso, "¿Llegó mi espejo? Mi cédula es 1.234.567", consultas=bloque, ahora=AHORA))
    assert r == {"decir": "Ya lo busco en el sistema, un momento.", "accion": "consultar"}
    assert recepcion.consulta_pendiente(caso) == {"que": "pedidos", "documento": "1234567"}
    assert caso.nombre == "Ramón" and '"consultar"' in pedidos_a_claude[0][0]

    vistos = []
    r2 = asyncio.run(recepcion.continuar(caso, lambda p: vistos.append(p) or "Solicitud SOL-1: espejo, llega en 15 días",
                                         ahora=AHORA))
    assert vistos == [{"que": "pedidos", "documento": "1234567"}]
    assert "llega aproximadamente" in r2["decir"] and recepcion.consulta_pendiente(caso) is None
    assert [l["quien"] for l in caso.conversacion] == ["agente", "cliente", "agente", "sistema", "agente"]
    # La segunda vez Claude ve el resultado y no tiene cómo volver a consultar en el mismo turno.
    assert "Sistema (resultado de tu consulta" in pedidos_a_claude[1][1] and '"consultar"' not in pedidos_a_claude[1][0]
    assert asyncio.run(recepcion.continuar(caso, lambda p: "x")) == {"decir": "", "accion": "seguir"}


def test_el_cliente_simulado_no_ve_lo_que_devolvio_el_sistema(monkeypatch):
    vistos: list[str] = []

    async def complete(system, user, *, max_tokens=200):
        vistos.append(user)
        return "Gracias"

    monkeypatch.setattr(llm, "complete", complete)
    caso = recepcion.nuevo(Campaign(id=3, name="R", modo="recepcion"), "simulacion")
    recepcion.decir(caso, "agente", "Hola")
    recepcion.decir(caso, "sistema", "Cliente encontrado: Ramón, CI 1234567")
    asyncio.run(recepcion.cliente_simulado(caso, "Quiere su espejo"))
    assert "1234567" not in vistos[0]
