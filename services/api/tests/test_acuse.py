"""Escucha activa en la encuesta (app/acuse.py): qué dice el agente después de cada respuesta."""
from __future__ import annotations

import asyncio

import pytest

from app import acuse
from app.models import QuestionType
from app.services import llm
from app.config import settings
from tests.test_ficha_agente import _con_pregunta, base, cliente  # noqa: F401

INTERNO = {"X-Callbot-Token": settings.internal_token}


@pytest.mark.parametrize("texto,esperado", [
    ("Muy bien, gracias. ¿Y con la atención del personal que lo recibió?", "¿Y con la atención del personal que lo recibió?"),
    ("Perfecto. ¿Y con el plazo de entrega?", "¿Y con el plazo de entrega?"),
    ("Gracias. ¿Qué tan claro le resultó el presupuesto?", "¿Qué tan claro le resultó el presupuesto?"),
    ("¡Excelente! Muchas gracias. ¿Nos recomendaría?", "¿Nos recomendaría?"),
    ("Ya casi terminamos. ¿Qué tan probable es que nos recomiende?", "Ya casi terminamos. ¿Qué tan probable es que nos recomiende?"),
    ("Para terminar, ¿hay algo que podríamos mejorar?", "Para terminar, ¿hay algo que podríamos mejorar?"),
    ("Primero, ¿qué tan satisfecho quedó?", "Primero, ¿qué tan satisfecho quedó?"),
    ("Gracias.", "Gracias."),  # si no queda pregunta, no se toca
])
def test_saca_solo_la_transicion_fija(texto, esperado):
    assert acuse.sin_acuse(texto) == esperado


@pytest.mark.parametrize("valor,qtype,banda", [
    (10, QuestionType.SCALE_1_10, "alta"), (9, QuestionType.SCALE_1_10, "alta"), (8, QuestionType.SCALE_1_10, "media"),
    (7, QuestionType.SCALE_1_10, "media"), (6, QuestionType.SCALE_1_10, "baja"), (1, QuestionType.SCALE_1_10, "baja"),
    (5, QuestionType.SCALE_1_5, "alta"), (2, QuestionType.SCALE_1_5, "baja"), (10, QuestionType.YES_NO, "neutra"),
    (None, QuestionType.OPEN, "abierta"), (None, QuestionType.SCALE_1_10, "neutra"),
])
def test_bandas(valor, qtype, banda):
    assert acuse.banda(valor, qtype) == banda


def test_solo_el_puntaje_va_la_fija_sin_claude(monkeypatch):
    async def complete(*a, **k):
        raise AssertionError("no hacía falta Claude")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    texto, banda = asyncio.run(acuse.acuse("¿Del uno al diez?", QuestionType.SCALE_1_10, "Uno, muy mal.", 1, 1))
    assert banda == "baja" and texto in acuse.FIJAS["baja"]
    texto, _ = asyncio.run(acuse.acuse("¿Del uno al diez?", QuestionType.SCALE_1_10, "Un diez", 10, 2))
    assert texto in acuse.FIJAS["alta"]


def test_si_conto_algo_mas_claude_lo_retoma(monkeypatch):
    pedidos = []

    async def complete(system, user, *, max_tokens=200):
        pedidos.append(user)
        return "Lamento mucho que le hayan devuelto el auto sucio."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    texto, banda = asyncio.run(acuse.acuse("¿Qué tan satisfecho quedó?", QuestionType.SCALE_1_10,
                                           "Uno, me devolvieron el auto todo sucio.", 1, 1))
    assert texto == "Lamento mucho que le hayan devuelto el auto sucio." and banda == "baja"
    assert "todo sucio" in pedidos[0] and "Puntaje que dio: 1" in pedidos[0]


@pytest.mark.parametrize("respuesta", ["¿Me cuenta qué pasó?", "", None, "x" * 300])
def test_si_claude_pregunta_o_se_va_de_largo_va_la_fija(monkeypatch, respuesta):
    async def complete(system, user, *, max_tokens=200):
        return respuesta

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    texto, _ = asyncio.run(acuse.acuse("¿Algo para mejorar?", QuestionType.OPEN, "Todo hay que mejorar.", None, 6))
    assert texto in acuse.FIJAS["abierta"]


def test_si_claude_tarda_va_la_fija(monkeypatch):
    async def complete(system, user, *, max_tokens=200):
        await asyncio.sleep(5)

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    monkeypatch.setattr(acuse, "ESPERA_CLAUDE", 0.05)
    texto, _ = asyncio.run(acuse.acuse("¿Algo para mejorar?", QuestionType.OPEN, "Que me avisen cuando está listo.",
                                       None, 6))
    assert texto in acuse.FIJAS["abierta"]


def test_endpoint_y_guion(cliente, base):  # noqa: F811
    _con_pregunta(base)
    r = cliente.post("/internal/sessions/00000000-0000-0000-0000-000000000000/acuse",
                     json={"question_id": 1, "transcript": "Uno."}, headers=INTERNO)
    assert r.status_code == 200 and r.json()["banda"] == "baja" and r.json()["acuse"] in acuse.FIJAS["baja"]
    # Las frases fijas y las preguntas sin transición se precalientan: salen con la voz buena y sin espera.
    prompts = str(cliente.get("/internal/prompts", headers=INTERNO).json())
    assert "¡Qué bueno escuchar eso!" in prompts
