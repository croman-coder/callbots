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


def test_la_conversacion_sin_claude_no_responde(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    assert not conversation.is_configured()
    assert asyncio.run(conversation.reply("p", "d")) is None


def test_el_analisis_usa_claude_y_registra_el_modelo(monkeypatch):
    _responder(_ok('```json\n{"sentiment":"positivo","summary":"Todo bien","topics":["atención"],"requires_followup":false}\n```'), monkeypatch)
    resultado, modelo = analysis.query_llm("P: ¿ok?\nR: sí")
    assert resultado["sentiment"] == "positivo" and modelo == "claude-haiku-5-5"


def test_el_analisis_sin_json_de_claude_queda_solo_con_las_reglas(monkeypatch):
    _responder(_ok("lo siento, no puedo"), monkeypatch)
    assert analysis.query_llm("x") == (None, None)


def test_el_analisis_sin_nadie_que_conteste_devuelve_nada(monkeypatch):
    _responder(lambda r: httpx.Response(500), monkeypatch)
    assert analysis.query_llm("x") == (None, None)


def test_sin_clave_de_claude_no_hay_analisis(monkeypatch):
    monkeypatch.setattr(llm.settings, "anthropic_api_key", "")
    assert analysis.query_llm("x") == (None, None)


# ---------------------------------------------------------------- respuestas rápidas, sin pensar de más
# Medido el 21/10/2026: con Haiku 5.5 el pensamiento viene prendido y en 200 tokens se los gastaba todos pensando
# (stop_reason "max_tokens", ningún bloque de texto): el bot nunca contestaba una pregunta del cliente.
@pytest.mark.parametrize("modelo,pensar,esfuerzo,extra_tokens", [
    ("claude-haiku-5-5", {"type": "disabled"}, "low", False),
    ("claude-sonnet-5", {"type": "disabled"}, "low", False),
    ("claude-opus-5", {"type": "disabled"}, "low", False),
    ("claude-opus-4-8", {"type": "disabled"}, "low", False),
    ("claude-sonnet-5-5", {"type": "between_tools"}, "low", False),  # ahí "disabled" es un 400
    ("claude-opus-5-5", None, "low", True),    # no se puede apagar: esfuerzo bajo y lugar para pensar
    ("claude-fable-5-1", None, "low", True),
    ("claude-haiku-4-5", None, None, False),   # no piensa si no se le pide; `effort` le da error
])
def test_el_pedido_no_gasta_la_respuesta_pensando(monkeypatch, modelo, pensar, esfuerzo, extra_tokens):
    monkeypatch.setattr(llm.settings, "llm_model", modelo)
    _, cuerpo = llm._request("s", "u", 200)
    assert cuerpo.get("thinking") == pensar
    assert (cuerpo.get("output_config") or {}).get("effort") == esfuerzo
    assert (cuerpo["max_tokens"] > 200) is extra_tokens


def test_si_se_le_acaban_los_tokens_pensando_avisa_y_usa_la_frase_fija(monkeypatch, caplog):
    _responder(lambda r: httpx.Response(200, json={"stop_reason": "max_tokens", "content": [{"type": "thinking", "thinking": ""}]}), monkeypatch)
    with caplog.at_level("WARNING"):
        assert asyncio.run(llm.complete("s", "u")) is None
    assert "max_tokens" in caplog.text


# ---------------------------------------------------------------- caché y consumo (31/10/2026)
def test_lo_fijo_va_al_cache_y_lo_variable_aparte():
    _, cuerpo = llm._request("x" * llm.MIN_CACHE, "u", 100, variable="Información: abre a las 7.")
    fijo, variable = cuerpo["system"]
    assert fijo["cache_control"] == {"type": "ephemeral"} and "cache_control" not in variable
    assert variable["text"] == "Información: abre a las 7."
    # Corto y con algo variable: dos bloques, ninguno al caché.
    _, cuerpo = llm._request("corto", "u", 100, variable="v")
    assert [b.get("cache_control") for b in cuerpo["system"]] == [None, None]


def test_cada_pedido_deja_su_consumo_con_la_llamada(monkeypatch, caplog):
    uso = {"input_tokens": 92, "cache_read_input_tokens": 3518, "cache_creation_input_tokens": 0, "output_tokens": 132}
    _responder(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "Hola"}], "usage": uso}),
               monkeypatch)
    marca = llm.contexto.set("caso 12")
    try:
        with caplog.at_level("INFO", logger="app.services.llm"):
            assert asyncio.run(llm.complete("s", "u", tipo="recepción")) == "Hola"
    finally:
        llm.contexto.reset(marca)
    linea = next(r.getMessage() for r in caplog.records if "Claude uso" in r.getMessage())
    assert linea == "Claude uso [caso 12] recepción: entrada 92, caché leída 3518, caché escrita 0, salida 132"


def test_la_api_anota_la_llamada_de_cada_pedido():
    from app import main

    assert main._RUTA_DE_LLAMADA.match("/internal/casos/12/turno").groups() == ("casos", "12")
    m = main._RUTA_DE_LLAMADA.match("/internal/sessions/1a2b3c4d-0000-4000-8000-000000000000/acuse")
    assert m.group(1) == "sessions" and m.group(2)[:8] == "1a2b3c4d"
    assert main._RUTA_DE_LLAMADA.match("/campaigns/1") is None
