"""Webhooks de resultados: JSON firmado a otro sistema después de cada llamada, solo a direcciones públicas."""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json

import httpx
import pytest

from app import agentes, webhooks
from app.models import Answer, CallAnalysis, CallAttempt, CallOutcome, Campaign, Question, WebhookEnvio
from app.scheduler import tasks
from tests.test_buzon_y_transferencia import INTERNO, Sesion, _agente, api  # noqa: F401
from tests.test_ficha_agente import H, base, cliente  # noqa: F401

PUBLICA = "93.184.216.34"
REAL_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _dns(monkeypatch):
    """hooks.example.com resuelve a una IP pública; todo lo demás, a lo que diga el sistema (nombres internos fallan)."""
    real = agentes.resolver_publico

    def resolver(host):
        return PUBLICA if host == "hooks.example.com" else real(host)

    monkeypatch.setattr(agentes, "resolver_publico", resolver)


# ─────────────────────────────────────────────── direcciones
@pytest.mark.parametrize("url", ["http://postgres:5432/x", "http://127.0.0.1/hook", "http://10.0.0.5/hook", "ftp://hooks.example.com"])
def test_no_se_aceptan_direcciones_internas(url):
    with pytest.raises(webhooks.WebhookInvalido):
        webhooks.validar_url(url)


def test_una_direccion_publica_se_acepta_y_normaliza():
    assert webhooks.validar_url("hooks.example.com/callbot") == "https://hooks.example.com/callbot"


# ─────────────────────────────────────────────── envío
def test_el_envio_va_a_la_ip_validada_con_la_firma(monkeypatch):
    vistos = []

    def manejar(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(webhooks.httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(manejar), **kw))
    estado, error, ms = webhooks.enviar("https://hooks.example.com/callbot", {"evento": "prueba", "x": "ñ"}, "secreto")
    assert (estado, error) == (200, None) and ms >= 0
    req = vistos[0]
    assert req.url.host == PUBLICA and req.headers["host"] == "hooks.example.com"
    esperado = "sha256=" + hmac.new(b"secreto", req.content, hashlib.sha256).hexdigest()
    assert req.headers["x-callbot-firma"] == esperado and req.headers["x-callbot-evento"] == "prueba"
    assert json.loads(req.content) == {"evento": "prueba", "x": "ñ"}


@pytest.mark.parametrize("respuesta,error", [
    (lambda r: httpx.Response(500), "Respondió 500"),
    (lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("lento")), "No respondió"),
    (lambda r: (_ for _ in ()).throw(httpx.ConnectError("no")), "No se pudo conectar"),
])
def test_un_envio_que_falla_no_levanta_y_dice_por_que(monkeypatch, respuesta, error):
    monkeypatch.setattr(webhooks.httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(respuesta), **kw))
    estado, motivo, _ = webhooks.enviar("https://hooks.example.com/x", {"evento": "prueba"}, "s")
    assert motivo.startswith(error)


def test_una_redireccion_a_un_lugar_interno_se_corta(monkeypatch):
    monkeypatch.setattr(webhooks.httpx, "Client", lambda **kw: REAL_CLIENT(
        transport=httpx.MockTransport(lambda r: httpx.Response(307, headers={"location": "http://postgres:5432/"})), **kw))
    estado, error, _ = webhooks.enviar("https://hooks.example.com/x", {"evento": "prueba"}, "s")
    assert estado is None and error


# ─────────────────────────────────────────────── qué se manda
def _llamada_completa(S):
    cid, sesion = _agente(S)
    s = S()
    call = s.query(CallAttempt).filter_by(session_uuid=sesion).one()
    q = s.query(Question).filter_by(campaign_id=cid).one()
    call.outcome, call.ended_at, call.duration_seconds = CallOutcome.COMPLETED, dt.datetime.now(dt.timezone.utc), 70
    s.add(Answer(call_id=call.id, question_id=q.id, transcript="un ocho", value_numeric=8.0))
    s.add(CallAnalysis(call_id=call.id, satisfaction_score=8.0, sentiment="neutral", summary="Bien, con demora.",
                       topics=["demora"], requires_followup=True, followup_reason="8/10"))
    call.target.datos = {"modelo": "Kardian"}
    s.commit()
    return cid, call.id


def test_el_json_tiene_todo_lo_de_la_llamada(Sesion):
    cid, call_id = _llamada_completa(Sesion)
    s = Sesion()
    datos = webhooks.payload(s.get(CallAttempt, call_id), s.get(Campaign, cid))
    assert datos["evento"] == "llamada_terminada" and datos["llamada"]["resultado"] == "completed"
    assert datos["cliente"]["telefono"] == "+595981000001" and datos["cliente"]["datos"]["modelo"] == "Kardian"
    assert datos["respuestas"][0]["transcripcion"] == "un ocho" and datos["respuestas"][0]["puntaje_10"] == 8
    assert datos["analisis"]["requiere_seguimiento"] is True and datos["analisis"]["temas"] == ["demora"]
    json.dumps(datos)  # serializable


# ─────────────────────────────────────────────── después de cada llamada
def test_la_tarea_manda_solo_a_los_activos_y_registra(Sesion, monkeypatch):
    cid, call_id = _llamada_completa(Sesion)
    s = Sesion()
    c = s.get(Campaign, cid)
    c.webhooks = [{"url": "https://hooks.example.com/a", "activo": True}, {"url": "https://hooks.example.com/b", "activo": False}]
    s.commit()
    enviados = []
    monkeypatch.setattr(webhooks, "enviar", lambda url, datos, secreto: enviados.append((url, secreto)) or (200, None, 12))
    assert tasks.enviar_webhooks(call_id) == {"enviados": 1}
    assert [u for u, _ in enviados] == ["https://hooks.example.com/a"] and enviados[0][1]  # con secreto generado
    e = Sesion().query(WebhookEnvio).one()
    assert (e.estado, e.prueba, e.call_id) == (200, False, call_id)


def test_cerrar_la_llamada_encola_el_envio_si_hay_webhooks(Sesion, monkeypatch):
    cid, call_id = _llamada_completa(Sesion)
    s = Sesion()
    s.get(Campaign, cid).webhooks = [{"url": "https://hooks.example.com/a", "activo": True}]
    s.commit()
    encolados = []
    monkeypatch.setattr(tasks, "analyze_call", lambda db, call: None)
    monkeypatch.setattr(tasks, "push_result_to_bitrix", lambda db, call: True)
    monkeypatch.setattr(tasks.enviar_webhooks, "delay", lambda i: encolados.append(i))
    tasks.finalize_call(call_id)
    assert encolados == [call_id]


# ─────────────────────────────────────────────── en la ficha
def test_cargar_probar_pausar_y_borrar(cliente, base, monkeypatch):
    r = cliente.post("/campaigns/1/webhooks", data={"url": "https://hooks.example.com/callbot"}, headers=H)
    assert "ok=" in r.headers["location"] and r.headers["location"].endswith("#webhooks")
    c = base().get(Campaign, 1)
    assert c.webhooks == [{"url": "https://hooks.example.com/callbot", "activo": True}] and len(c.webhook_secreto) == 48
    assert "error=" in cliente.post("/campaigns/1/webhooks", data={"url": "http://postgres:5432"}, headers=H).headers["location"]

    monkeypatch.setattr(webhooks, "enviar", lambda url, datos, secreto: (200, None, 15))
    r = cliente.post("/campaigns/1/webhooks/0/probar", headers=H).json()
    assert r["ok"] and r["estado"] == 200 and "ejemplo" in r["con"]
    assert base().query(WebhookEnvio).one().prueba is True

    pagina = cliente.get("/campaigns/1?tab=analisis").text
    assert "hooks.example.com/callbot" in pagina and "Probar con la última llamada" in pagina and "Últimos envíos" in pagina

    cliente.post("/campaigns/1/webhooks/0/activar", headers=H)
    assert base().get(Campaign, 1).webhooks[0]["activo"] is False
    viejo = base().get(Campaign, 1).webhook_secreto
    cliente.post("/campaigns/1/webhooks/secreto", headers=H)
    assert base().get(Campaign, 1).webhook_secreto != viejo
    cliente.post("/campaigns/1/webhooks/0/borrar", headers=H)
    assert base().get(Campaign, 1).webhooks is None
