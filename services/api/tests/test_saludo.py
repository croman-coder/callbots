"""El «¿tiene un minutito?» del saludo: qué hacer con lo que contesta el cliente, y la diferencia entre «no me
llamen más» (baja) y «ahora no puedo» (se lo llama en otro momento)."""
from __future__ import annotations

import uuid

import pytest

from app.models import SurveyTarget, TargetStatus
from app.services import conversation, scoring
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
    assert _saludo(api, sesion, "Sí, dale") == {"accion": "seguir"}
    assert _saludo(api, sesion, "") == {"accion": "seguir"}


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

    async def falso(pregunta, dijo, intento=0, extra_prompt=None):
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
    assert _saludo(api, sesion, "¿Quién habla?") == {"accion": "seguir"}


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
