"""Prueba del 3/11/2026 con la campaña «Entrega de vehículo»: el cliente no quedó conforme y el agente no lo notó.

- «La verdad que no, no tan satisfecho» se anotó como 8: «satisfecho» pesaba más que el «no».
- A eso le contestó «Bien, lo anoto.» (Claude no llegó a tiempo y la fija de la nota media es neutra).
- A «No, tampoco fue claro» le prometió que «un asesor lo va a contactar», y nadie lo iba a llamar.
"""
from __future__ import annotations

import asyncio

import pytest

from app import acuse
from app.models import QuestionType
from app.routers.internal import FRASE_NO_ENTENDI
from app.services import conversation, llm, scoring
from tests.test_buzon_y_transferencia import Sesion, _agente, api  # noqa: F401  (fixtures)

E = QuestionType.SCALE_1_10
P = "Primero, ¿qué tan satisfecho quedó con el proceso de entrega de su vehículo?"


# ─────────────────────────────────────────────── la nota
@pytest.mark.parametrize("dijo", [
    "En la verdad que no, no tan satisfecho.", "No muy conforme.", "No quedé satisfecho.", "Para nada satisfecho.",
    "No fue tan bueno.", "Tampoco bien.", "No estuvo tan mal.", "Ni bien ni mal.", "No me fue bien.",
])
def test_una_calificacion_negada_no_se_anota_se_pregunta_el_numero(dijo):
    r = scoring.interpret(dijo, E)
    assert r.understood is False and r.value is None, (dijo, r)


@pytest.mark.parametrize("dijo,valor", [
    ("No, muy bien.", 9),  # el «no» contesta otra cosa: la coma lo separa
    ("Muy bien.", 9),
    ("Satisfecho.", 8),
    ("No me gustó.", 2),
    ("No tuve ningún problema, excelente.", 10),
    ("Siete, no tan satisfecho.", 7),  # con número manda el número
])
def test_lo_que_no_es_una_negacion_se_anota_como_siempre(dijo, valor):
    r = scoring.interpret(dijo, E)
    assert r.understood and r.value == valor, (dijo, r)


# ─────────────────────────────────────────────── el tono
@pytest.mark.parametrize("dijo", [
    "Siete, no tan satisfecho.", "Ocho, no muy conforme la verdad.", "No, tampoco fue claro.",
    "Siete, no me gustó cómo me atendieron.", "Siete, podría haber sido mejor.",
])
def test_el_disgusto_suave_es_disconforme(dijo):
    assert acuse.emocion(7, E, dijo) == "disconforme"


@pytest.mark.parametrize("dijo,valor,animo", [
    ("No, muy bien todo.", 9, "contento"),
    ("Ocho, más o menos.", 8, "neutro"),  # «más o menos» acá es «aproximadamente»
    ("Nueve, aunque la espera no fue tan corta.", 9, "neutro"),  # nota alta con un pero: ni festejo ni pésame
])
def test_lo_que_no_es_disgusto(dijo, valor, animo):
    assert acuse.emocion(valor, E, dijo) == animo


def test_si_claude_no_llega_al_disconforme_le_va_una_fija_con_empatia(monkeypatch):
    async def lento(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(acuse, "ESPERA_CLAUDE", 0.05)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", lento)
    r = asyncio.run(acuse.responder(P, E, "Siete, no tan satisfecho la verdad.", 7, 0))
    assert r["emocion"] == "disconforme" and r["acuse"] in acuse.FIJAS["disconforme"]
    assert "Bien, lo anoto." not in acuse.FIJAS["disconforme"]


def test_a_claude_se_le_dice_que_esta_disconforme(monkeypatch):
    pedidos = []

    async def complete(system, user, **k):
        pedidos.append(user)
        return "Lamento que la entrega no haya sido como esperaba."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    asyncio.run(acuse.responder(P, E, "Siete, no tan satisfecho la verdad.", 7, 0))
    assert "Cómo está: disconforme." in pedidos[0]


def test_las_fijas_del_disconforme_se_precalientan():
    assert set(acuse.FIJAS["disconforme"]) <= set(acuse.todas_las_fijas())


def test_claude_tiene_dos_segundos_para_el_acuse():
    # Medido el 3/11/2026: Haiku contesta en 0,9 a 1,1 s, con picos de más de 1,6 s (dos de cuatro en la prueba).
    assert acuse.ESPERA_CLAUDE == 2.0


# ─────────────────────────────────────────────── la respuesta libre
def test_ante_una_queja_no_promete_que_lo_van_a_llamar(monkeypatch):
    async def complete(system, user, **k):
        return ("Lamentamos que no le haya quedado clara la explicación, y un asesor lo va a contactar. "
                "Del cero al diez, ¿qué nota le pondría?")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    texto = asyncio.run(conversation.reply("¿Y con la explicación que le dieron?", "No, tampoco fue claro."))
    assert texto == ("Lamentamos que no le haya quedado clara la explicación. "
                     "Del cero al diez, ¿qué nota le pondría?")


def test_si_pregunta_un_precio_si_puede_decir_que_lo_contacta_un_asesor(monkeypatch):
    respuesta = "Eso se lo va a confirmar un asesor, que lo va a contactar. ¿Qué tan satisfecho quedó?"

    async def complete(system, user, **k):
        return respuesta

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    assert asyncio.run(conversation.reply(P, "¿Cuánto sale el service de los 10.000?")) == respuesta


def test_si_todo_era_la_promesa_se_usa_la_frase_fija(monkeypatch):
    async def complete(system, user, **k):
        return "Un asesor lo va a llamar."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    assert asyncio.run(conversation.reply(P, "No me gustó.")) is None


def test_las_instrucciones_solo_permiten_el_asesor_si_pregunta_algo_concreto():
    assert "Ante una queja" in conversation.INSTRUCCION_BASE
    assert "rioplatense" not in conversation.INSTRUCCION_BASE and "Paraguay" in conversation.INSTRUCCION_BASE


def test_a_claude_se_le_dice_la_escala_aunque_la_pregunta_no_la_diga(Sesion, api, monkeypatch):  # noqa: F811
    pedidos = []

    async def complete(system, user, **k):
        pedidos.append(user)
        return "Entiendo que no quedó del todo conforme. Del cero al diez, ¿qué nota le pondría?"

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    _, sesion = _agente(Sesion)
    r = api.post(f"/internal/sessions/{sesion}/reply", json={
        "question_text": P, "transcript": "La verdad que no, no tan satisfecho.", "retries_used": 0,
        "question_type": "scale_1_10"}).json()
    assert r["reply"].endswith("¿qué nota le pondría?")
    assert "del cero al diez" in pedidos[0]


# ─────────────────────────────────────────────── la que no se entendió
def test_la_frase_de_no_entendi_se_precalienta(Sesion, api):  # noqa: F811
    _agente(Sesion)
    assert FRASE_NO_ENTENDI in api.get("/internal/prompts").json()["texts"]
