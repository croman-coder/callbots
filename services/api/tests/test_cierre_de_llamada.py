"""Una encuesta cuenta como completada solo si el cliente dijo algo."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace as N

import pytest

from app.models import CallOutcome, TargetStatus
from app.scheduler import tasks


def _resp(transcript=None, valor=None):
    return N(transcript=transcript, value_numeric=valor)


@pytest.mark.parametrize("respuestas,cuantas", [
    ([_resp(), _resp("  ")], 0),            # filas vacías: el cliente atendió y no dijo nada
    ([_resp("un diez"), _resp()], 1),
    ([_resp(None, 8.0)], 1),                # valor interpretado sin transcripción
    ([], 0),
])
def test_respuestas_reales(respuestas, cuantas):
    assert len(tasks.respuestas_reales(N(answers=respuestas))) == cuantas


@pytest.fixture()
def cierre(monkeypatch):
    registro = {"reintentos": [], "analizadas": 0}
    llamada = N(outcome=CallOutcome.PARTIAL, answers=[], target=N(id=1, status=TargetStatus.CALLING), analysis=None)

    @contextmanager
    def sesion():
        yield N(get=lambda _m, _i: llamada, flush=lambda: None)

    monkeypatch.setattr(tasks, "session_scope", sesion)
    monkeypatch.setattr(tasks, "analyze_call", lambda db, c: registro.__setitem__("analizadas", registro["analizadas"] + 1))
    monkeypatch.setattr(tasks, "push_result_to_bitrix", lambda db, c: None)
    monkeypatch.setattr(tasks, "_schedule_retry_or_finish", lambda t, estado: registro["reintentos"].append(estado))
    return llamada, registro


def test_atendio_pero_no_dijo_nada_se_reintenta_y_no_cuenta_como_encuesta(cierre):
    llamada, registro = cierre
    llamada.answers = [_resp(), _resp()]
    tasks.finalize_call.run(1) if hasattr(tasks.finalize_call, "run") else tasks.finalize_call(1)
    assert registro["reintentos"] == [TargetStatus.NO_ANSWER] and registro["analizadas"] == 0
    assert llamada.target.status is TargetStatus.CALLING  # no quedó COMPLETED


def test_con_una_respuesta_real_se_guarda_y_se_cierra(cierre):
    llamada, registro = cierre
    llamada.answers = [_resp("un siete"), _resp()]
    tasks.finalize_call.run(1) if hasattr(tasks.finalize_call, "run") else tasks.finalize_call(1)
    assert llamada.target.status is TargetStatus.COMPLETED and registro["analizadas"] == 1 and registro["reintentos"] == []
