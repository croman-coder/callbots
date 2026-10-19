"""Claude (Haiku) para Callbot: una sola puerta para los dos usos del LLM.

  - conversación en llamada (async, con el cliente escuchando): `complete`
  - análisis posterior de la llamada (sync, sin apuro): `complete_sync`

Habla directo con la API de Messages con httpx, como el resto del proyecto: no
vale sumar el SDK por dos llamadas. La clave va en cabecera (`x-api-key`); httpx
loguea la URL completa de cada request y una clave por query terminaría en el log.

Nunca lanza: devuelve None si no hay clave, si tarda de más o si falla, y quien
llama usa su respaldo (la frase fija en llamada; las reglas en el análisis).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"


def is_configured() -> bool:
    return bool(settings.anthropic_api_key.strip())


def _request(system: str, user: str, max_tokens: int, temperature: float) -> tuple[dict[str, str], dict[str, Any]]:
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": VERSION,
        "content-type": "application/json",
    }
    body = {
        "model": settings.llm_model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": user}],
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
    return texto or None


async def complete(system: str, user: str, *, max_tokens: int = 200, temperature: float = 0.4) -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens, temperature)
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


def complete_sync(system: str, user: str, *, max_tokens: int = 500, temperature: float = 0.1) -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens, temperature)
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
    try:
        obj = json.loads(texto[ini : fin + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
