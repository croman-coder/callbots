"""Buzón de voz y transferencia a un asesor: guion, respuestas, cierre, reintentos y configuración."""
from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager
from types import SimpleNamespace as N

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as dbmod, main, models, sso
from app.config import settings
from app.models import (
    Answer,
    CallAttempt,
    CallOutcome,
    Campaign,
    Question,
    QuestionType,
    SurveyTarget,
    TargetStatus,
)
from app.scheduler import tasks
from app.services import asterisk_ari
from app.services.transferir import pide_persona

H = {"Sec-Fetch-Site": "same-origin"}
INTERNO = {"X-Callbot-Token": "token-interno-de-prueba"}


# ------------------------------------------------------------------ pide_persona
@pytest.mark.parametrize(
    "texto",
    [
        "Quiero hablar con un asesor",
        "quiero hablar con una persona",
        "Pásame con alguien por favor",
        "comuníqueme con un ejecutivo",
        "hábleme con un asesor",
        "me pasa con el encargado",
        "no quiero hablar con una máquina",
        "transfiérame con un supervisor",
        "necesito un asesor",
    ],
)
def test_pide_una_persona(texto):
    assert pide_persona(texto)


@pytest.mark.parametrize(
    "texto",
    [
        "un diez",
        "muy buena la atención del asesor",
        "me atendió una persona muy amable",
        "ya hablé con el asesor ayer",
        "no quiero hablar con nadie",
        "¿usted es una persona?",
        "no gracias, no me llamen más",
        "",
        None,
    ],
)
def test_no_pide_una_persona(texto):
    assert not pide_persona(texto)


# ------------------------------------------------------------------ endpoints internos
@pytest.fixture()
def Sesion(monkeypatch):
    motor = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(motor)
    S = sessionmaker(motor, expire_on_commit=False)

    def get_db():
        s = S()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[dbmod.get_db] = get_db
    monkeypatch.setattr(dbmod, "SessionLocal", S)
    yield S
    main.app.dependency_overrides.pop(dbmod.get_db, None)


def _agente(S, **campos) -> tuple[int, uuid.UUID]:
    """Un agente con una pregunta y un destinatario en llamada. SQLite devuelve las fechas sin zona
    horaria (Postgres no), así que la hora de inicio se deja vacía: el cierre usa «ahora» en su lugar."""
    s = S()
    c = Campaign(name="Posventa", description="", bitrix_entity_type_id=2, trigger_field="x", delay_hours=48,
                 is_active=True, **campos)
    s.add(c)
    s.flush()
    s.add(Question(campaign_id=c.id, position=1, text="¿Del cero al diez?", qtype=QuestionType.SCALE_1_10))
    ahora = dt.datetime.now(dt.timezone.utc)
    t = SurveyTarget(campaign_id=c.id, phone="+595981000001", contact_name="Marta Gómez", trigger_at=ahora,
                     scheduled_at=ahora, status=TargetStatus.CALLING, attempts=1)
    s.add(t)
    s.flush()
    sesion = uuid.uuid4()
    s.add(CallAttempt(session_uuid=sesion, target_id=t.id, started_at=None, answered_at=ahora,
                      asterisk_channel_id="1792548822.0"))
    s.commit()
    return c.id, sesion


@pytest.fixture()
def api(Sesion):
    return TestClient(main.app, follow_redirects=False, headers=INTERNO)


def test_el_guion_lleva_buzon_y_transferencia(Sesion, api):
    _, sesion = _agente(Sesion, voicemail_action="mensaje", voicemail_script="Dejo mi mensaje.",
                        transfer_number="+595981777888", transfer_script="Un momento.")
    g = api.get(f"/internal/sessions/{sesion}/script").json()
    assert g["voicemail_action"] == "mensaje" and g["voicemail_script"] == "Dejo mi mensaje."
    assert g["transfer_enabled"] is True and g["transfer_script"] == "Un momento."


def test_por_defecto_todo_apagado(Sesion, api):
    _, sesion = _agente(Sesion)
    g = api.get(f"/internal/sessions/{sesion}/script").json()
    assert g["voicemail_action"] is None and g["transfer_enabled"] is False


def _responder(api, sesion, pregunta_id, texto):
    return api.post(f"/internal/sessions/{sesion}/answers", json={
        "question_id": pregunta_id, "transcript": texto, "asr_confidence": 0.9, "duration_seconds": 2.0, "retries_used": 0,
    }).json()


def test_pedir_un_asesor_solo_vale_si_hay_a_quien_pasarle_la_llamada(Sesion, api):
    _, con = _agente(Sesion, transfer_number="+595981777888")
    _, sin = _agente(Sesion)
    assert _responder(api, con, 1, "quiero hablar con un asesor")["wants_transfer"] is True
    assert _responder(api, con, 1, "un nueve")["wants_transfer"] is False
    assert _responder(api, sin, 2, "quiero hablar con un asesor")["wants_transfer"] is False


def test_quien_pide_no_ser_llamado_no_se_transfiere(Sesion, api):
    _, sesion = _agente(Sesion, transfer_number="+595981777888")
    r = _responder(api, sesion, 1, "no me llamen más, quiero hablar con una persona para que me saque de la lista")
    # Si el cliente además pide salir, manda salir (optout): el agente no lo transfiere.
    assert not (r["is_optout"] and r["wants_transfer"])


class _AriFalso:
    llamadas: list = []
    falla = False

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def build_endpoint(self, phone):
        return f"PJSIP/{phone.lstrip('+')}@troncal"

    def set_global(self, nombre, valor):
        if self.falla:
            raise asterisk_ari.AriError("Asterisk caído")
        self.llamadas.append((nombre, valor))


@pytest.fixture()
def ari(monkeypatch):
    _AriFalso.llamadas = []
    _AriFalso.falla = False
    monkeypatch.setattr(asterisk_ari, "AriClient", _AriFalso)
    return _AriFalso


def test_transferir_deja_el_destino_en_asterisk_para_esa_sesion(Sesion, api, ari):
    _, sesion = _agente(Sesion, transfer_number="+595981777888")
    assert api.post(f"/internal/sessions/{sesion}/transfer").json() == {"ok": True}
    assert ari.llamadas == [(f"TRANSFER_{sesion}", "PJSIP/595981777888@troncal")]


def test_si_asterisk_no_responde_devuelve_ok_false_y_no_error(Sesion, api, ari):
    ari.falla = True
    _, sesion = _agente(Sesion, transfer_number="+595981777888")
    r = api.post(f"/internal/sessions/{sesion}/transfer")
    assert r.status_code == 200 and r.json()["ok"] is False


def test_sin_asesor_cargado_no_se_transfiere(Sesion, api, ari):
    _, sesion = _agente(Sesion)
    assert api.post(f"/internal/sessions/{sesion}/transfer").json()["ok"] is False and ari.llamadas == []


def test_en_una_prueba_no_se_marca_a_nadie(Sesion, api, ari):
    cid, _ = _agente(Sesion, transfer_number="+595981777888")
    assert api.post(f"/internal/sessions/{uuid.UUID(int=cid)}/transfer").json()["demo"] is True and ari.llamadas == []


def test_los_prompts_incluyen_las_frases_nuevas_solo_si_estan_activas(Sesion, api):
    _agente(Sesion, voicemail_action="mensaje", voicemail_script="Frase de buzón.",
            transfer_number="+595981777888", transfer_script="Frase de transferencia.")
    textos = api.get("/internal/prompts").json()["texts"]
    assert "Frase de buzón." in textos and "Frase de transferencia." in textos
    assert any("no puedo comunicarlo" in t for t in textos)


def _cerrar(api, sesion, outcome, causa=None):
    return api.post(f"/internal/sessions/{sesion}/finished", json={
        "outcome": outcome, "questions_asked": 0, "questions_answered": 0, "hangup_cause": causa,
    })


def test_un_buzon_no_cuenta_como_llamada_atendida(Sesion, api, monkeypatch):
    monkeypatch.setattr(tasks.finalize_call, "delay", lambda *a, **k: None)
    _, sesion = _agente(Sesion)
    assert _cerrar(api, sesion, "voicemail", "buzon_colgo").json()["outcome"] == "voicemail"
    llamada = Sesion().scalar(__import__("sqlalchemy").select(CallAttempt).where(CallAttempt.session_uuid == sesion))
    assert llamada.answered_at is None and llamada.outcome is CallOutcome.VOICEMAIL
    assert llamada.hangup_cause == "buzon_colgo"


def test_una_transferencia_se_guarda_como_tal(Sesion, api, monkeypatch):
    monkeypatch.setattr(tasks.finalize_call, "delay", lambda *a, **k: None)
    _, sesion = _agente(Sesion)
    assert _cerrar(api, sesion, "transferred", "transferida").json()["outcome"] == "transferred"
    llamada = Sesion().scalar(__import__("sqlalchemy").select(CallAttempt).where(CallAttempt.session_uuid == sesion))
    assert llamada.answered_at is not None  # a un asesor sí lo atendió una persona


# ------------------------------------------------------------------ qué pasa con el destinatario después
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


def test_un_buzon_se_vuelve_a_llamar(cierre):
    llamada, registro = cierre
    llamada.outcome = CallOutcome.VOICEMAIL
    tasks.finalize_call.run(1)
    assert registro["reintentos"] == [TargetStatus.NO_ANSWER] and llamada.target.status is TargetStatus.CALLING


def test_lo_transferido_no_se_vuelve_a_llamar(cierre):
    llamada, registro = cierre
    llamada.outcome = CallOutcome.TRANSFERRED
    tasks.finalize_call.run(1)
    assert registro["reintentos"] == [] and llamada.target.status is TargetStatus.COMPLETED and registro["analizadas"] == 0


def test_lo_transferido_que_alcanzo_a_contestar_se_guarda(cierre):
    llamada, registro = cierre
    llamada.outcome = CallOutcome.TRANSFERRED
    llamada.answers = [N(transcript="un siete", value_numeric=7.0)]
    tasks.finalize_call.run(1)
    assert registro["analizadas"] == 1 and llamada.target.status is TargetStatus.COMPLETED


def test_con_asesor_se_origina_por_la_variante_del_dialplan_y_sin_asesor_por_la_de_siempre(monkeypatch):
    capturado = {}

    class Ari(asterisk_ari.AriClient):
        def __init__(self):
            pass

        def _request(self, metodo, ruta, **kw):
            capturado.update(kw["json"])
            return N(json=lambda: {"id": "123.0"})

        def build_endpoint(self, phone):
            return "PJSIP/x"

    ari = Ari()
    ari.originate("+595981000001", uuid.uuid4())
    assert capturado["extension"] == settings.asterisk_extension
    ari.originate("+595981000001", uuid.uuid4(), extension="start_transferible")
    assert capturado["extension"] == "start_transferible"


# ------------------------------------------------------------------ configuración desde la ficha
@pytest.fixture()
def cliente(Sesion):
    cid, _ = _agente(Sesion)
    c = TestClient(main.app, follow_redirects=False)
    c.cookies.set(sso.COOKIE, sso.emitir_sesion(sso.Principal("ana@santarosa.com.py", "admin", "crm"), settings.sso_secret))
    c.cid = cid
    return c


def _guardar(c, **campos):
    return c.post(f"/campaigns/{c.cid}/guardar", data={"tab": "configuracion", **campos}, headers=H)


def test_la_ficha_muestra_los_controles_y_el_estado(cliente):
    html = cliente.get(f"/campaigns/{cliente.cid}?tab=configuracion").text
    assert 'name="voicemail_action"' in html and 'name="transfer_number"' in html and "apagada" in html


def test_guardar_el_buzon_y_el_asesor(cliente, Sesion):
    r = _guardar(cliente, voicemail_action="mensaje", voicemail_script="Hola, lo volvemos a llamar.",
                 transfer_number="0981 777 888", transfer_script="Ya lo comunico.")
    assert r.status_code == 303 and "error=" not in r.headers["location"]
    c = Sesion().get(Campaign, cliente.cid)
    assert c.voicemail_action == "mensaje" and c.voicemail_script == "Hola, lo volvemos a llamar."
    assert c.transfer_number == "+595981777888" and c.transfer_script == "Ya lo comunico."
    assert "activa" in cliente.get(f"/campaigns/{cliente.cid}?tab=configuracion").text


def test_vaciar_el_telefono_apaga_la_transferencia(cliente, Sesion):
    _guardar(cliente, transfer_number="0981777888")
    _guardar(cliente, transfer_number="")
    assert Sesion().get(Campaign, cliente.cid).transfer_number is None


def test_un_telefono_invalido_no_se_guarda(cliente, Sesion):
    r = _guardar(cliente, transfer_number="hola")
    assert "error=" in r.headers["location"]
    assert Sesion().get(Campaign, cliente.cid).transfer_number is None


def test_dejar_mensaje_exige_el_mensaje(cliente, Sesion):
    r = _guardar(cliente, voicemail_action="mensaje", voicemail_script="")
    assert "error=" in r.headers["location"]
    assert Sesion().get(Campaign, cliente.cid).voicemail_action is None


def test_una_accion_de_buzon_desconocida_se_rechaza(cliente):
    assert "error=" in _guardar(cliente, voicemail_action="borrar").headers["location"]


def test_colgar_no_pide_mensaje(cliente, Sesion):
    r = _guardar(cliente, voicemail_action="colgar", voicemail_script="")
    assert "error=" not in r.headers["location"] and Sesion().get(Campaign, cliente.cid).voicemail_action == "colgar"


def test_en_pantalla_el_buzon_y_la_transferencia_hablan_en_espanol():
    from app.etiquetas import etiqueta

    assert etiqueta(CallOutcome.VOICEMAIL) == "buzón de voz" and etiqueta(CallOutcome.TRANSFERRED) == "transferida"
    assert etiqueta("buzon_mensaje", "corte") == "buzón de voz, dejó mensaje"
    assert etiqueta("transferencia_fallida", "corte") == "no se pudo transferir"
