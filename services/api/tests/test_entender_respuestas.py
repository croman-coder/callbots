"""Segunda prueba del 3/11/2026: el agente no entendía respuestas claras.

- «También un desastre.» se anotó 8: «tam*bien*» tiene «bien» adentro.
- «No, que fue un desastre.» no se entendió: «desastre» no estaba en las reglas, y la respuesta se perdió.
- «Nunca.» a «¿qué tan probable es que nos recomiende?» no se entendió y se volvió a preguntar.

Las reglas ahora miran palabras enteras y saben más extremos; y cuando no alcanzan, Claude entiende la respuesta (con
tope de tiempo). Si es ambigua, Claude no inventa: se vuelve a preguntar.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app import acuse, entender
from app.models import Answer, CallAttempt, QuestionType
from app.services import conversation, llm, scoring
from tests.test_buzon_y_transferencia import Sesion, _agente, _responder, api  # noqa: F401  (fixtures)

E = QuestionType.SCALE_1_10


@pytest.fixture(autouse=True)
def _sin_memoria():
    entender._en_curso.clear()
    yield
    entender._en_curso.clear()


# ─────────────────────────────────────────────── las reglas
@pytest.mark.parametrize("dijo,valor", [
    ("También un desastre.", 0),
    ("No, que fue un desastre.", 0),
    ("Un asco.", 0),
    ("Fatal.", 0),
    ("Una porquería.", 0),
    ("Nunca.", 0),
    ("Jamás.", 0),
    ("No, nunca.", 0),
    ("Nunca jamás.", 0),
    ("Bien.", 8),
    ("Excelente.", 10),
])
def test_respuestas_claras_sin_numero(dijo, valor):
    r = scoring.interpret(dijo, E)
    assert r.understood and r.value == valor, (dijo, r)


@pytest.mark.parametrize("dijo", [
    "También.",  # tiene «bien» adentro, pero no es una nota
    "Nunca tuve problemas con ellos.",  # «nunca» no es la respuesta: hay que preguntar el número
    "No fue un desastre.",  # negado: no se sabe cuánto
])
def test_lo_que_no_es_una_nota_clara_no_se_anota(dijo):
    r = scoring.interpret(dijo, E)
    assert r.value is None, (dijo, r)


# ─────────────────────────────────────────────── Claude cuando las reglas no alcanzan
def _claude(monkeypatch, devuelve, demora=0.0):
    pedidos = []

    async def complete(system, user, **k):
        pedidos.append((system, user, k.get("tipo")))
        if demora:
            await asyncio.sleep(demora)
        return devuelve

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    return pedidos


def _valor_guardado(S, sesion):
    s = S()
    call = s.scalar(select(CallAttempt).where(CallAttempt.session_uuid == sesion))
    return s.scalar(select(Answer).where(Answer.call_id == call.id))


def test_si_las_reglas_no_entienden_claude_pone_la_nota(Sesion, api, monkeypatch):
    pedidos = _claude(monkeypatch, '{"valor": 0}')
    _, sesion = _agente(Sesion)
    r = _responder(api, sesion, 1, "Lo peor que me pasó en la vida.")
    assert r["understood"] is True and r["value"] == 0 and r["should_retry"] is False
    assert "Lo peor que me pasó en la vida." in pedidos[0][1] and "del 0 al 10" in pedidos[0][0]
    respuesta = _valor_guardado(Sesion, sesion)
    assert respuesta.value_numeric == 0 and respuesta.value_source == "claude"  # se sabe quién la puso


def test_si_claude_dice_que_no_es_una_nota_se_vuelve_a_preguntar(Sesion, api, monkeypatch):
    _claude(monkeypatch, '{"valor": null}')
    _, sesion = _agente(Sesion)
    r = _responder(api, sesion, 1, "Ehh, fondz, nada más.")
    assert r["understood"] is False and r["should_retry"] is True


@pytest.mark.parametrize("devuelve", ['{"valor": 15}', '{"valor": "mucho"}', "no sé", None])
def test_si_claude_devuelve_algo_que_no_sirve_se_vuelve_a_preguntar(Sesion, api, monkeypatch, devuelve):
    _claude(monkeypatch, devuelve)
    _, sesion = _agente(Sesion)
    assert _responder(api, sesion, 1, "Lo peor que me pasó en la vida.")["should_retry"] is True


def test_si_claude_tarda_se_vuelve_a_preguntar(Sesion, api, monkeypatch):
    _claude(monkeypatch, '{"valor": 0}', demora=1)
    monkeypatch.setattr(entender, "ESPERA_CLAUDE", 0.05)
    _, sesion = _agente(Sesion)
    assert _responder(api, sesion, 1, "Lo peor que me pasó en la vida.")["should_retry"] is True


@pytest.mark.parametrize("dijo", ["Nueve.", "No tan satisfecho.", "", "No me llamen más"])
def test_claude_no_se_usa_si_las_reglas_alcanzan_o_si_es_ambiguo(Sesion, api, monkeypatch, dijo):
    # Con número, en silencio o con una baja manda la regla. «No tan satisfecho» no tiene número que adivinar:
    # se pregunta (ver test_disconformidad.py), sin gastar un segundo en Claude.
    pedidos = _claude(monkeypatch, '{"valor": 3}')
    _, sesion = _agente(Sesion)
    _responder(api, sesion, 1, dijo)
    assert pedidos == []


def test_el_acuse_usa_la_misma_nota_sin_volver_a_preguntarle_a_claude(Sesion, api, monkeypatch):
    # El voice-agent pide el acuse y guarda la respuesta a la vez: los dos tienen que ver la misma nota.
    pedidos = _claude(monkeypatch, '{"valor": 0}')
    _, sesion = _agente(Sesion)
    _responder(api, sesion, 1, "Lo peor que me pasó en la vida.")
    r = api.post(f"/internal/sessions/{sesion}/acuse",
                 json={"question_id": 1, "transcript": "Lo peor que me pasó en la vida.", "ya_dichos": []}).json()
    assert r["banda"] == "baja"
    assert [p for p in pedidos if p[2] == "entender"] == pedidos[:1]  # una sola vez


def test_en_una_prueba_tambien_entiende(Sesion, api, monkeypatch):
    _claude(monkeypatch, '{"valor": 0}')
    _agente(Sesion)
    r = _responder(api, uuid.UUID(int=1), 1, "Lo peor que me pasó en la vida.")
    assert r["understood"] is True and r["value"] == 0


def test_las_instrucciones_piden_no_adivinar():
    assert "null" in entender.SISTEMA and "no tan satisfecho" in entender.SISTEMA


# ─────────────────────────────────────────────── el tono y la respuesta libre
def test_enojado_con_nota_media_tiene_respaldo_con_empatia(monkeypatch):
    async def lento(*a, **k):
        await asyncio.sleep(1)

    monkeypatch.setattr(acuse, "ESPERA_CLAUDE", 0.05)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", lento)
    r = asyncio.run(acuse.responder("¿Del cero al diez?", E, "Siete, la atención un desastre.", 7, 0))
    assert r["emocion"] == "enojado" and r["acuse"] in acuse.FIJAS["disconforme"]


def test_claude_no_dice_en_que_parte_de_la_encuesta_estan():
    # En la P4 de 5 dijo «Para cerrar la encuesta…».
    assert "para cerrar" in conversation.INSTRUCCION_BASE
