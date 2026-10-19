"""Claude Haiku en Callbot: sin red, con transportes simulados."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.services import analysis, conversation, llm

REAL_ASYNC = httpx.AsyncClient


def _responder(manejar, monkeypatch):
    transporte = httpx.MockTransport(manejar)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: REAL_ASYNC(transport=transporte, **kw))
    monkeypatch.setattr(llm.httpx, "post", lambda url, **kw: httpx.Client(transport=transporte).post(url, **{k: v for k, v in kw.items() if k != "timeout"}))


def _ok(texto):
    return lambda req: httpx.Response(200, json={"content": [{"type": "text", "text": texto}]})


@pytest.fixture(autouse=True)
def _clave(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "sk-ant-clave-de-prueba-larga")
    monkeypatch.setattr(llm.settings, "llm_model", "claude-haiku-5-5")


def test_sin_clave_no_hace_ningun_pedido(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    visto = []
    _responder(lambda r: visto.append(r) or httpx.Response(200), monkeypatch)
    assert asyncio.run(llm.complete("s", "u")) is None and llm.complete_sync("s", "u") is None
    assert visto == []


def test_el_pedido_lleva_la_clave_en_cabecera_y_nunca_en_la_url(monkeypatch):
    visto = []
    _responder(lambda r: visto.append(r) or _ok("Hola")(r), monkeypatch)
    assert asyncio.run(llm.complete("Sos un bot.", "¿Qué hora es?", max_tokens=50)) == "Hola"
    req = visto[0]
    assert req.headers["x-api-key"] == "sk-ant-clave-de-prueba-larga" and req.headers["anthropic-version"]
    assert "sk-ant" not in str(req.url)
    cuerpo = json.loads(req.content)
    assert cuerpo["model"] == "claude-haiku-5-5" and cuerpo["system"] == "Sos un bot."
    assert "temperature" not in cuerpo  # los modelos Claude 5 la rechazan con 400
    assert cuerpo["max_tokens"] == 50 and cuerpo["messages"] == [{"role": "user", "content": "¿Qué hora es?"}]


@pytest.mark.parametrize("respuesta", [
    lambda r: httpx.Response(401, json={"error": {"message": "invalid x-api-key"}}),
    lambda r: httpx.Response(529, json={"error": {"type": "overloaded_error"}}),
    lambda r: httpx.Response(200, json={"content": []}),
    lambda r: httpx.Response(200, json={"algo": "raro"}),
    lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("lento")),
])
def test_cualquier_falla_devuelve_none_y_no_lanza(monkeypatch, respuesta):
    _responder(respuesta, monkeypatch)
    assert asyncio.run(llm.complete("s", "u")) is None
    assert llm.complete_sync("s", "u") is None


@pytest.mark.parametrize("texto,esperado", [
    ('{"sentiment": "positivo"}', {"sentiment": "positivo"}),
    ('```json\n{"sentiment": "neutral", "topics": ["a"]}\n```', {"sentiment": "neutral", "topics": ["a"]}),
    ('Acá va el análisis: {"requires_followup": true} listo.', {"requires_followup": True}),
    ("no hay json", None), ("[1, 2]", None), ('{"roto": ', None), ("", None), (None, None),
])
def test_parse_json_object(texto, esperado):
    assert llm.parse_json_object(texto) == esperado


def test_la_conversacion_usa_claude_y_deja_texto_plano_y_corto(monkeypatch):
    _responder(_ok('  "Entiendo.   ' + "Es una frase larga. " * 40 + '"  '), monkeypatch)
    t = asyncio.run(conversation.reply("¿Cómo califica la atención?", "¿cuánto sale?"))
    assert t and len(t) <= 401 and "  " not in t and not t.startswith('"') and t.endswith(".")


def test_la_conversacion_sin_claude_ni_gemini_no_responde(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    monkeypatch.setattr(conversation.settings, "gemini_api_key", "")
    assert not conversation.is_configured()
    assert asyncio.run(conversation.reply("p", "d")) is None


def test_el_analisis_usa_claude_y_registra_el_modelo(monkeypatch):
    _responder(_ok('```json\n{"sentiment":"positivo","summary":"Todo bien","topics":["atención"],"requires_followup":false}\n```'), monkeypatch)
    resultado, modelo = analysis.query_llm("P: ¿ok?\nR: sí")
    assert resultado["sentiment"] == "positivo" and modelo == "claude-haiku-5-5"


def test_el_analisis_cae_a_ollama_si_claude_no_da_json(monkeypatch):
    _responder(_ok("lo siento, no puedo"), monkeypatch)
    monkeypatch.setattr(analysis, "query_ollama", lambda *a, **k: {"sentiment": "neutral"})
    monkeypatch.setattr(analysis.settings, "ollama_model", "granite")
    resultado, modelo = analysis.query_llm("x")
    assert resultado == {"sentiment": "neutral"} and modelo == "granite"


def test_el_analisis_sin_nadie_que_conteste_devuelve_nada(monkeypatch):
    _responder(lambda r: httpx.Response(500), monkeypatch)
    monkeypatch.setattr(analysis, "query_ollama", lambda *a, **k: None)
    assert analysis.query_llm("x") == (None, None)
