"""Revisión a mano: corregir el resultado, corregir una respuesta mal transcrita y comentar cada llamada."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app import main, revision, sso
from app.config import settings
from app.models import Answer, CallAnalysis, CallAttempt, CallComment, CallOutcome, Question, QuestionType
from app.scheduler import tasks
from tests.test_buzon_y_transferencia import INTERNO, Sesion, _agente, api  # noqa: F401

H = {"Sec-Fetch-Site": "same-origin"}


def _cliente(usuario="ana@santarosa.com.py", rol="admin"):
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal(usuario, rol, "crm"), settings.sso_secret))
    return c


def _llamada(S, outcome=CallOutcome.COMPLETED, valor=10.0):
    cid, sesion = _agente(S)
    s = S()
    call = s.query(CallAttempt).filter_by(session_uuid=sesion).one()
    q = s.query(Question).filter_by(campaign_id=cid).one()
    call.outcome, call.ended_at = outcome, dt.datetime.now(dt.timezone.utc)
    s.add(Answer(call_id=call.id, question_id=q.id, transcript="diez", value_numeric=valor, value_source="rules"))
    s.add(CallAnalysis(call_id=call.id, satisfaction_score=valor, sentiment="positivo"))
    s.commit()
    return cid, call.id


def test_exitosa_es_la_del_sistema_salvo_revision():
    c = CallAttempt(outcome=CallOutcome.COMPLETED)
    assert revision.exitosa(c)
    revision.revisar(c, "no_exitosa", "Contestó en joda", "ana")
    assert not revision.exitosa(c) and c.revision_por == "ana" and c.revision_at
    revision.revisar(c, "sistema", "", "ana")
    assert revision.exitosa(c) and c.revision is None


@pytest.mark.parametrize("veredicto,motivo,error", [("exitosa", "", "motivo"), ("inventado", "x" * 10, "Elegí")])
def test_revisar_valida(veredicto, motivo, error):
    with pytest.raises(revision.RevisionError, match=error):
        revision.revisar(CallAttempt(), veredicto, motivo, "ana")


@pytest.mark.parametrize("texto,qtype,esperado", [
    ("6", QuestionType.SCALE_1_10, 6.0), ("0", QuestionType.SCALE_1_10, 0.0), ("sí", QuestionType.YES_NO, 1.0),
    ("no", QuestionType.YES_NO, 0.0), ("4", QuestionType.SCALE_1_5, 4.0),
])
def test_valor_de(texto, qtype, esperado):
    assert revision.valor_de(texto, qtype) == esperado


@pytest.mark.parametrize("texto,qtype", [("11", QuestionType.SCALE_1_10), ("abc", QuestionType.SCALE_1_10),
                                          ("tal vez", QuestionType.YES_NO), ("5", QuestionType.OPEN)])
def test_valor_de_rechaza(texto, qtype):
    with pytest.raises(revision.RevisionError):
        revision.valor_de(texto, qtype)


def test_revisar_desde_el_detalle_y_el_filtro_de_exitosas(Sesion):
    cid, call_id = _llamada(Sesion)
    c = _cliente()
    r = c.post(f"/calls/{call_id}/revision", data={"veredicto": "no_exitosa", "motivo": "Contestó sin escuchar"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    call = Sesion().get(CallAttempt, call_id)
    assert call.revision == "no_exitosa" and call.revision_por == "ana@santarosa.com.py"
    pagina = c.get(f"/calls/{call_id}").text
    assert "Revisada a mano por ana@santarosa.com.py" in pagina and "Contestó sin escuchar" in pagina
    exitosas = c.get(f"/campaigns/{cid}?tab=registro&filtro=exitosas").text
    assert "revisada" not in exitosas  # ya no cuenta como exitosa
    assert "error=" in c.post(f"/calls/{call_id}/revision", data={"veredicto": "exitosa", "motivo": ""}, headers=H).headers["location"]


def test_corregir_una_respuesta_guarda_el_original_y_recalcula(Sesion, monkeypatch):
    _, call_id = _llamada(Sesion, valor=10.0)
    encolados = []
    monkeypatch.setattr(tasks.reanalyze_call, "delay", lambda i: encolados.append(i))
    a = Sesion().query(Answer).filter_by(call_id=call_id).one()
    c = _cliente()
    r = c.post(f"/calls/{call_id}/answers/{a.id}/corregir", data={"valor": "6"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    a = Sesion().get(Answer, a.id)
    assert (a.value_numeric, a.valor_original, a.value_source, a.corregido_por) == (6.0, 10.0, "manual", "ana@santarosa.com.py")
    assert Sesion().query(CallAnalysis).filter_by(call_id=call_id).one().satisfaction_score == 6.0
    assert encolados == [call_id]  # se recalcula el análisis y se vuelve a mandar a Bitrix
    # una segunda corrección conserva el valor original de verdad
    c.post(f"/calls/{call_id}/answers/{a.id}/corregir", data={"valor": "7"}, headers=H)
    assert Sesion().get(Answer, a.id).valor_original == 10.0
    assert "era 10" in c.get(f"/calls/{call_id}").text
    assert "error=" in c.post(f"/calls/{call_id}/answers/{a.id}/corregir", data={"valor": "15"}, headers=H).headers["location"]


def test_comentarios_cada_uno_borra_los_suyos(Sesion):
    _, call_id = _llamada(Sesion)
    ana, beto = _cliente(), _cliente("beto@santarosa.com.py", "calidad")
    beto.post(f"/calls/{call_id}/comentarios", data={"texto": "Le devolví la llamada."}, headers=H)
    com = Sesion().query(CallComment).one()
    assert com.autor == "beto@santarosa.com.py" and "Le devolví la llamada." in ana.get(f"/calls/{call_id}").text
    otro = _cliente("carla@santarosa.com.py", "calidad")
    assert otro.post(f"/calls/{call_id}/comentarios/{com.id}/borrar", headers=H).status_code == 403
    ana.post(f"/calls/{call_id}/comentarios/{com.id}/borrar", headers=H)  # un administrador puede
    assert Sesion().query(CallComment).count() == 0
    assert "error=" in beto.post(f"/calls/{call_id}/comentarios", data={"texto": "  "}, headers=H).headers["location"]


def test_bitrix_avisa_que_se_corrigio_a_mano():
    from types import SimpleNamespace as N

    from app.services import writeback

    q = N(position=1, text="¿Del cero al diez?", qtype=QuestionType.SCALE_1_10, counts_for_score=True)
    call = N(target=N(phone="+595981", contact_name="X"), analysis=None, started_at=dt.datetime(2026, 10, 21, 10, 0),
             duration_seconds=60, attempt_number=1, questions_answered=1, questions_asked=1,
             answers=[N(question=q, value_numeric=6.0, transcript="seis", value_source="manual", corregido_por="ana")])
    assert "Corregido a mano en el panel por ana" in writeback.build_comment(call)
