"""El «¿tiene un minutito?» del saludo: qué hacer con lo que contesta el cliente, y la diferencia entre «no me
llamen más» (baja) y «ahora no puedo» (se lo llama en otro momento)."""
from __future__ import annotations

import uuid

import pytest

from app import saludo
from app.models import SurveyTarget, TargetStatus
from app.routers.internal import FRASE_EMPIEZO
from app.services import conversation, llm, scoring
from tests.test_buzon_y_transferencia import INTERNO, Sesion, _agente, _responder, api  # noqa: F401  (fixtures)


@pytest.mark.parametrize("texto", [
    "No me llamen más", "no me interesa", "Sáquenme de la lista", "Número equivocado", "no quiero que me llamen",
    "por favor no me molesten",
])
def test_baja(texto):
    assert scoring.detect_baja(texto) and scoring.detect_optout(texto) and not scoring.detect_ocupado(texto)


@pytest.mark.parametrize("texto", [
    "Ahora no puedo", "estoy manejando", "Llamame más tarde", "estoy en una reunión", "en este momento no",
    "no es buen momento", "llame mañana por favor", "después me llaman",
])
def test_ocupado(texto):
    assert scoring.detect_ocupado(texto) and scoring.detect_optout(texto) and not scoring.detect_baja(texto)


@pytest.mark.parametrize("texto", ["Sí, dale", "Hola, sí, dígame", "bueno", "un nueve", "no, todo bien", "no sé"])
def test_ni_baja_ni_ocupado(texto):
    assert not scoring.detect_baja(texto) and not scoring.detect_ocupado(texto)


# ─────────────────────────────────────────────── endpoint del saludo
def _saludo(api, sesion, texto):
    return api.post(f"/internal/sessions/{sesion}/greeting", json={"transcript": texto}).json()


def _estado(S, sesion):
    from app.models import CallAttempt

    s = S()
    call = s.query(CallAttempt).filter_by(session_uuid=sesion).one()
    return s.get(SurveyTarget, call.target_id).status


def test_si_dale_sigue(Sesion, api):
    _, sesion = _agente(Sesion)
    r = _saludo(api, sesion, "Sí, dale")
    assert r["accion"] == "seguir" and r["decir"] in saludo.FIJAS  # se le agradece antes de la primera pregunta
    assert _saludo(api, sesion, "") == {"accion": "seguir"}  # en silencio el agente dice su propio puente


def test_no_me_llamen_da_de_baja(Sesion, api):
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, "No me llamen más, por favor")["accion"] == "baja"
    assert _estado(Sesion, sesion) is TargetStatus.OPTED_OUT


def test_ahora_no_puedo_reprograma_sin_dar_de_baja(Sesion, api):
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, "Ahora no puedo, estoy manejando")["accion"] == "reprogramar"
    assert _estado(Sesion, sesion) is TargetStatus.CALLING


def test_pedir_un_asesor_solo_si_hay_a_quien(Sesion, api):
    _, con = _agente(Sesion, transfer_number="+595981777888")
    _, sin = _agente(Sesion)
    assert _saludo(api, con, "Quiero hablar con un asesor")["accion"] == "asesor"
    assert _saludo(api, sin, "Quiero hablar con un asesor")["accion"] != "asesor"


def test_una_pregunta_la_contesta_claude_y_vuelve_a_pedir_el_minuto(Sesion, api, monkeypatch):
    pedidos = []

    async def falso(pregunta, dijo, intento=0, extra_prompt=None, conocimiento=""):
        pedidos.append((pregunta, dijo))
        return "Le habla el asistente de Santa Rosa, por su visita al taller. ¿Tiene un minuto?"

    monkeypatch.setattr(conversation, "reply", falso)
    _, sesion = _agente(Sesion)
    r = _saludo(api, sesion, "¿Quién habla?")
    assert r["accion"] == "responder" and r["respuesta"].endswith("¿Tiene un minuto?")
    assert pedidos and "minuto" in pedidos[0][0] and pedidos[0][1] == "¿Quién habla?"
    # sin signo de pregunta, igual se reconoce
    assert _saludo(api, sesion, "de parte de quién")["accion"] == "responder"


def test_si_claude_no_contesta_se_sigue(Sesion, api, monkeypatch):
    async def nada(*a, **k):
        return None

    monkeypatch.setattr(conversation, "reply", nada)
    _, sesion = _agente(Sesion)
    # Preguntó algo y no hubo respuesta: ni «¡Perfecto, gracias!» (no dijo que sí) ni arrancar en seco.
    assert _saludo(api, sesion, "¿Quién habla?") == {"accion": "seguir", "decir": FRASE_EMPIEZO}


# ─────────────────────────────────────────────── seguir el hilo después del «¿tiene un minutito?»
# Prueba del 3/11/2026: el cliente dijo que sí y el agente largó «Primero, ¿qué tan satisfecho…?» sin agradecer.
@pytest.mark.parametrize("texto", ["No, gracias.", "No.", "No, muchas gracias", "Nop", "no no"])
def test_un_no_al_minuto_corta_amable_y_se_lo_llama_otro_dia(Sesion, api, texto):
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, texto)["accion"] == "reprogramar"
    assert _estado(Sesion, sesion) is TargetStatus.CALLING  # no es una baja


@pytest.mark.parametrize("texto", ["No hay problema", "No, dale, pregunte", "no, todo bien", "No sé", "Sí, tengo"])
def test_lo_que_no_es_un_no_sigue(Sesion, api, texto):
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, texto)["accion"] == "seguir"


def test_un_si_simple_se_agradece_con_una_frase_fija_sin_esperar_a_claude(Sesion, api, monkeypatch):
    async def no_llamar(*a, **k):
        pytest.fail("un «sí» no necesita a Claude")

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", no_llamar)
    _, sesion = _agente(Sesion)
    r = _saludo(api, sesion, "Sí, tengo.")
    assert r["accion"] == "seguir" and r["decir"] in saludo.FIJAS


def test_si_acepta_contando_algo_claude_lo_retoma(Sesion, api, monkeypatch):
    pedidos = []

    async def falso(sistema, pedido, **k):
        pedidos.append(pedido)
        return "Gracias, va a ser bien rapidito."

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", falso)
    _, sesion = _agente(Sesion)
    r = _saludo(api, sesion, "Sí, dale, pero que sea rápido por favor")
    assert r == {"accion": "seguir", "decir": "Gracias, va a ser bien rapidito."}
    assert "que sea rápido" in pedidos[0]


@pytest.mark.parametrize("devuelve", [None, "¿Cuál es su nombre?", "Gracias. " * 30])
def test_si_claude_no_sirve_va_la_frase_fija(Sesion, api, monkeypatch, devuelve):
    async def falso(*a, **k):
        return devuelve

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", falso)
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, "Sí, dale, pero que sea rápido por favor")["decir"] in saludo.FIJAS


def test_si_claude_tarda_va_la_frase_fija(Sesion, api, monkeypatch):
    import asyncio

    async def lento(*a, **k):
        await asyncio.sleep(1)
        return "Gracias, va a ser rapidito."

    monkeypatch.setattr(saludo, "ESPERA_CLAUDE", 0.05)
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", lento)
    _, sesion = _agente(Sesion)
    assert _saludo(api, sesion, "Sí, dale, pero que sea rápido por favor")["decir"] in saludo.FIJAS


def test_el_precalentado_incluye_las_frases_del_saludo(Sesion, api):
    _agente(Sesion)
    textos = api.get("/internal/prompts").json()["texts"]
    assert set(saludo.FIJAS) <= set(textos) and FRASE_EMPIEZO in textos


def test_en_una_prueba_no_se_marca_a_nadie(Sesion, api):
    _agente(Sesion)
    assert _saludo(api, uuid.UUID(int=1), "No me llamen más")["accion"] == "baja"  # y no rompe por no haber llamada


# ─────────────────────────────────────────────── en medio de la encuesta
def test_ocupado_en_una_pregunta_corta_sin_dar_de_baja(Sesion, api):
    _, sesion = _agente(Sesion)
    r = _responder(api, sesion, 1, "Ahora no puedo, estoy manejando")
    assert r["is_optout"] is True  # corta la encuesta…
    assert _estado(Sesion, sesion) is TargetStatus.CALLING  # …pero se lo vuelve a llamar


def test_baja_en_una_pregunta_sigue_dando_de_baja(Sesion, api):
    _, sesion = _agente(Sesion)
    _responder(api, sesion, 1, "No me llamen más")
    assert _estado(Sesion, sesion) is TargetStatus.OPTED_OUT


def test_el_precalentado_incluye_la_frase_de_reprogramar(Sesion, api):
    from app.routers.internal import FRASE_LLAMAR_DESPUES

    _agente(Sesion)
    textos = api.get("/internal/prompts").json()["texts"]
    assert FRASE_LLAMAR_DESPUES in textos


def test_con_seseo_dies_es_diez():
    """Whisper escribe «Dies» cuando la «z» suena «s» (prueba del 2/11/2026): vale 10, no «no entendí»."""
    from app.models import QuestionType

    for texto in ("¡Dies!", "dies", "Le doy un dies."):
        r = scoring.interpret(texto, QuestionType.SCALE_1_10)
        assert r.value == 10, (texto, r)
