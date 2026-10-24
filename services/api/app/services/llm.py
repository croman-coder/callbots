"""Claude (Haiku) para Callbot: una sola puerta para los dos usos del LLM.

  - conversación en llamada (async, con el cliente escuchando): `complete`
  - análisis posterior de la llamada (sync, sin apuro): `complete_sync`

Habla directo con la API de Messages con httpx, como el resto del proyecto: no
vale sumar el SDK por dos llamadas. La clave va en cabecera (`x-api-key`); httpx
loguea la URL completa de cada request y una clave por query terminaría en el log.

Sin `temperature`: los modelos Claude 5 la rechazan con un 400 ("deprecated for this
model"), y un 400 acá significaba que el bot jamás usara el LLM sin que nada avisara.

Nunca lanza: devuelve None si no hay clave, si tarda de más o si falla, y quien
llama usa su respaldo (la frase fija en llamada; las reglas en el análisis).
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"


def is_configured() -> bool:
    return bool(settings.anthropic_api_key.strip())


# Lo que se le pide a Claude acá es corto y concreto (una respuesta por teléfono, un JSON con el análisis): pensar no
# mejora nada y cuesta segundos. Los modelos Claude 5 piensan por defecto, y en una respuesta de 200 tokens Haiku 5.5
# se los gastaba todos pensando: devolvía solo el bloque de pensamiento y el bot nunca contestaba (medido el
# 21/10/2026). Cada familia se apaga distinto, y mandarle lo que no acepta es un 400:
#   - Opus 5.5 y Fable: el pensamiento no se apaga; esfuerzo bajo y lugar de sobra para que igual quede texto.
#   - Sonnet 5.5: «disabled» es un 400; el modo sin pensar es «between_tools».
#   - Haiku 4.5 y anteriores: no piensan si no se les pide, y `effort` les da error.
#   - El resto (Haiku 5.5, Opus 5, Sonnet 5, Opus 4.6 a 4.8): «disabled», válido con esfuerzo bajo.
_SIEMPRE_PIENSAN = ("claude-opus-5-5", "claude-fable-", "claude-mythos-")
_SIN_ESFUERZO = ("claude-haiku-4", "claude-3", "claude-sonnet-4-5", "claude-opus-4-1", "claude-opus-4-0", "claude-sonnet-4-0")
MARGEN_PARA_PENSAR = 1024
# Caracteres desde los que conviene marcar el prompt para caché (~4.000 tokens, el mínimo de los modelos chicos).
MIN_CACHE = 16_000


def _sin_pensar(modelo: str, max_tokens: int) -> dict[str, Any]:
    """Los campos del pedido que dejan la respuesta corta y rápida en este modelo."""
    m = modelo.lower()
    if m.startswith(_SIN_ESFUERZO):
        return {"max_tokens": max_tokens}
    if m.startswith(_SIEMPRE_PIENSAN):
        return {"max_tokens": max_tokens + MARGEN_PARA_PENSAR, "output_config": {"effort": "low"}}
    if m.startswith("claude-sonnet-5-5"):
        return {"max_tokens": max_tokens, "thinking": {"type": "between_tools"}, "output_config": {"effort": "low"}}
    return {"max_tokens": max_tokens, "thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}


def _request(system: str, user: str, max_tokens: int) -> tuple[dict[str, str], dict[str, Any]]:
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": settings.llm_model,
        # Un prompt largo (instrucciones del agente + base de conocimiento) se guarda en caché: la próxima respuesta
        # del mismo agente lo lee de ahí, más rápido y ~10 veces más barato. Uno corto no llega al mínimo cacheable.
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if len(system) >= MIN_CACHE else system,
        "messages": [{"role": "user", "content": user}],
        **_sin_pensar(settings.llm_model, max_tokens),
    }
    return headers, body


def _text(data: Any) -> str | None:
    """El texto de la respuesta, o None si vino cortada por seguridad o sin texto."""
    try:
        partes = [b["text"] for b in data["content"] if b.get("type") == "text"]
    except (KeyError, TypeError):
        log.warning("Claude devolvió algo inesperado: %s", str(data)[:200])
        return None
    texto = "".join(partes).strip()
    if not texto and isinstance(data, dict) and data.get("stop_reason") == "max_tokens":
        # Se le acabaron los tokens antes de escribir: no es «no tenía nada que decir», es un pedido mal dimensionado.
        log.warning("Claude cortó por max_tokens sin escribir texto (modelo %s): se usa la frase fija", settings.llm_model)
    return texto or None


async def complete(system: str, user: str, *, max_tokens: int = 200) -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens)
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            r = await client.post(ENDPOINT, headers=headers, json=body)
            r.raise_for_status()
            return _text(r.json())
    except httpx.TimeoutException:
        log.warning("Claude no contestó en %ss: se usa la frase fija", settings.llm_timeout_seconds)
    except httpx.HTTPStatusError as exc:
        # El cuerpo del error de Anthropic dice el motivo (modelo, saldo, límite) y no lleva la clave.
        log.warning("Claude respondió %s: %s", exc.response.status_code, exc.response.text[:200])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Claude falló: %s", exc)
    return None


def complete_sync(system: str, user: str, *, max_tokens: int = 500) -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens)
    try:
        r = httpx.post(ENDPOINT, headers=headers, json=body, timeout=settings.llm_analysis_timeout_seconds)
        r.raise_for_status()
        return _text(r.json())
    except httpx.HTTPStatusError as exc:
        log.warning("Claude respondió %s: %s", exc.response.status_code, exc.response.text[:200])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Claude falló: %s", exc)
    return None


def parse_json_object(texto: str | None) -> dict[str, Any] | None:
    """El primer objeto JSON del texto. El modelo a veces lo envuelve en ```json o lo antecede de una frase."""
    if not texto:
        return None
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini < 0 or fin <= ini:
        return None
    candidato = texto[ini : fin + 1]
    # Estricto primero; si falla, lo que más se le escapa al modelo: saltos de línea dentro de un texto y comas de más.
    for intento in (candidato, re.sub(r",\s*([}\]])", r"\1", candidato)):
        try:
            obj = json.loads(intento, strict=False)
        except json.JSONDecodeError:
            continue
        return obj if isinstance(obj, dict) else None
    return None
