"""El tono del agente sigue a la emoción del cliente, con la encuesta del 0 al 10 (reunión del 30/10/2026).

Pedido: «adaptar el tono a la emoción del cliente (enojado, neutro o contento)» y «si el cliente está enojado o se
queja, primero atender eso (“Entiendo, ¿qué pasó?”) y recién después seguir». Estas pruebas lo demuestran respuesta
por respuesta; la prueba de punta a punta con voz es `make e2e E=emociones` (docs/emocion-y-tono.md).
"""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app import acuse
from app.models import Answer, CallAttempt, QuestionType
from app.services import llm
from tests.test_buzon_y_transferencia import Sesion, _agente, _responder, api  # noqa: F401

P = "¿Del cero al diez, qué tan satisfecho quedó con el servicio?"
E = QuestionType.SCALE_1_10


# Lo que contesta el cliente → cómo está, qué hace el agente. «claude» es que retoma lo que dijo, con la emoción como
# guía; «repregunta», que pregunta qué pasó antes de seguir.
CASOS = [
    ("Cero.", 0, "enojado", "repregunta"),
    ("Cero, pésimo.", 0, "enojado", "repregunta"),
    ("Un uno, un desastre.", 1, "enojado", "repregunta"),
    ("Dos, me tuvieron una semana esperando y nadie me avisó nada.", 2, "enojado", "claude"),
    ("Tres.", 3, "enojado", "repregunta"),
    ("Cinco.", 5, "neutro", "repregunta"),
    ("Seis, estoy re molesto con la demora.", 6, "enojado", "claude"),
    ("Siete.", 7, "neutro", "media"),
    ("Ocho, más o menos.", 8, "neutro", "media"),
    ("Ocho, la verdad que muy amables todos.", 8, "contento", "claude"),
    ("Nueve.", 9, "contento", "alta"),
    ("Diez.", 10, "contento", "alta"),
    ("Diez, excelente.", 10, "contento", "alta"),
    ("Diez, pero la espera fue terrible.", 10, "neutro", "claude"),
]


@pytest.fixture
def claude(monkeypatch):
    pedidos = []

    async def complete(system, user, *, max_tokens=200, **kw):
        pedidos.append((system, user))
        if "Cómo está: enojado" in user:
            return "Lamento mucho la espera y que nadie le haya avisado."
        if "Cómo está: contento" in user:
            return "¡Qué bueno que lo hayan atendido tan bien!"
        return "Gracias por contarme cómo le fue."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    return pedidos


@pytest.mark.parametrize("dijo,valor,animo,que_hace", CASOS)
def test_el_tono_sigue_a_la_emocion_del_cliente(claude, dijo, valor, animo, que_hace):
    r = asyncio.run(acuse.responder(P, E, dijo, valor, 1))
    assert r["emocion"] == animo
    if que_hace == "repregunta":
        assert r["repregunta"] is True and r["acuse"] in acuse.REPREGUNTAS and "?" in r["acuse"]
        assert claude == []  # sin esperar a Claude: es una frase fija, del caché
    elif que_hace == "claude":
        assert r["repregunta"] is False and f"Cómo está: {animo}." in claude[0][1]
    else:
        assert r["repregunta"] is False and r["acuse"] in acuse.FIJAS[que_hace]
    # Al enojado nunca se le festeja; al contento nunca se le da el pésame.
    if animo == "enojado":
        assert "¡" not in r["acuse"] and "bueno" not in r["acuse"].lower()
    if animo == "contento":
        assert "lament" not in r["acuse"].lower()


def test_pregunta_que_paso_una_sola_vez_por_llamada(claude):
    primera = asyncio.run(acuse.responder(P, E, "Uno.", 1, 1))
    segunda = asyncio.run(acuse.responder(P, E, "Dos.", 2, 2, [primera["acuse"]]))
    assert primera["repregunta"] and not segunda["repregunta"]
    assert segunda["acuse"] in acuse.FIJAS["baja"]


def test_las_preguntas_abiertas_y_si_no_no_repreguntan(claude):
    assert not asyncio.run(acuse.responder("¿Algo para mejorar?", QuestionType.OPEN, "Nada.", None, 6))["repregunta"]
    assert not asyncio.run(acuse.responder("¿Lo recomendaría?", QuestionType.YES_NO, "No.", 0, 4))["repregunta"]


def test_despues_de_que_conto_que_paso_claude_lo_retoma(monkeypatch):
    pedidos = []

    async def complete(system, user, *, max_tokens=200, **kw):
        pedidos.append((system, user))
        return "Lamento mucho que le hayan cobrado un repuesto que no le cambiaron."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    texto = asyncio.run(acuse.acuse_motivo("Me cobraron un filtro que no me cambiaron.", 1))
    assert texto.startswith("Lamento mucho que le hayan cobrado")
    assert "filtro" in pedidos[0][1] and "No prometas" in pedidos[0][0]


@pytest.mark.parametrize("respuesta", ["¿Quiere que lo llamen?", "", None])
def test_si_claude_pregunta_o_no_contesta_va_la_fija_del_motivo(monkeypatch, respuesta):
    async def complete(system, user, *, max_tokens=200, **kw):
        return respuesta

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    assert asyncio.run(acuse.acuse_motivo("Me cobraron de más.", 1)) in acuse.FIJAS_MOTIVO


def test_las_frases_de_la_repregunta_se_precalientan():
    assert set(acuse.REPREGUNTAS + acuse.FIJAS_MOTIVO) <= set(acuse.todas_las_fijas())


def test_el_motivo_queda_en_la_transcripcion_de_esa_respuesta(Sesion, api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    _, sesion = _agente(Sesion)
    s = Sesion()
    pregunta = s.scalar(select(CallAttempt).where(CallAttempt.session_uuid == sesion)).target.campaign.questions[0].id
    assert _responder(api, sesion, pregunta, "Uno.")["understood"]

    r = api.post(f"/internal/sessions/{sesion}/acuse", json={"question_id": pregunta, "transcript": "Uno."}).json()
    assert r["repregunta"] is True and r["emocion"] == "enojado"
    r = api.post(f"/internal/sessions/{sesion}/motivo",
                 json={"question_id": pregunta, "texto": "Me devolvieron el auto sucio.", "ya_dichos": [r["acuse"]]})
    assert r.status_code == 200 and r.json()["acuse"] in acuse.FIJAS_MOTIVO
    respuesta = Sesion().scalar(select(Answer).where(Answer.question_id == pregunta))
    assert respuesta.transcript == "Uno. | Qué pasó: Me devolvieron el auto sucio."
    assert respuesta.value_numeric == 1  # el puntaje no cambia
    # Repetido (un reintento de red) no se suma dos veces.
    api.post(f"/internal/sessions/{sesion}/motivo", json={"question_id": pregunta, "texto": "Otra vez."})
    assert Sesion().scalar(select(Answer).where(Answer.question_id == pregunta)).transcript.count("Qué pasó") == 1


def test_el_motivo_en_una_prueba_no_guarda_nada(api):  # noqa: F811
    r = api.post("/internal/sessions/00000000-0000-0000-0000-000000000000/motivo",
                 json={"question_id": 999, "texto": "Algo."})
    assert r.status_code == 404  # la pregunta tiene que existir
