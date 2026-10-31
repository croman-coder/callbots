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

import contextvars
import json
import re
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)

ENDPOINT = "https://api.anthropic.com/v1/messages"
VERSION = "2023-06-01"

# Qué llamada está atendiendo la API cuando le pide algo a Claude («sesión 1a2b3c4d», «caso 12»; lo pone main.py): va
# en el registro de consumo, para sumar los tokens de cada llamada (scripts/consumo_claude.py).
contexto: contextvars.ContextVar[str] = contextvars.ContextVar("llm_contexto", default="")


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
# Caracteres desde los que se marca el prompt para caché. Antes eran 16.000 y el de recepción (~8.000 caracteres,
# 3.518 tokens) nunca entraba: cada turno lo pagaba entero. Medido el 31/10/2026, Haiku 5.5 lo cachea y el turno
# siguiente lo lee a un décimo del precio. Por debajo del mínimo del modelo, la marca no hace nada (ni cobra de más).
MIN_CACHE = 3_000


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


def _request(system: str | list[str], user: str, max_tokens: int,
             variable: str = "") -> tuple[dict[str, str], dict[str, Any]]:
    """`system` es lo fijo del agente, en uno o más bloques; `variable`, lo que cambia en cada pedido (la fecha, la
    parte de la base de conocimiento que viene al caso). Lo fijo va al caché por bloques: si cambia el segundo, el
    primero se lee igual del caché."""
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": VERSION,
        "content-type": "application/json",
    }
    # Un prompt largo se guarda en caché: la próxima respuesta del mismo agente lo lee de ahí, más rápido y ~10 veces
    # más barato. Uno corto no llega al mínimo cacheable.
    bloques: list[dict[str, Any]] = []
    largo = 0
    for parte in [system] if isinstance(system, str) else [p for p in system if p]:
        largo += len(parte)
        bloque: dict[str, Any] = {"type": "text", "text": parte}
        if largo >= MIN_CACHE:
            bloque["cache_control"] = {"type": "ephemeral"}
        bloques.append(bloque)
    if variable:
        bloques.append({"type": "text", "text": variable})
    sencillo = isinstance(system, str) and len(bloques) == 1 and "cache_control" not in bloques[0]
    body = {
        "model": settings.llm_model,
        "system": system if sencillo else bloques,
        "messages": [{"role": "user", "content": user}],
        **_sin_pensar(settings.llm_model, max_tokens),
    }
    return headers, body


def _registrar_uso(data: Any, tipo: str) -> None:
    """Una línea por pedido con los tokens (scripts/consumo_claude.py las suma por llamada)."""
    uso = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(uso, dict):
        return
    log.info(
        "Claude uso [%s] %s: entrada %d, caché leída %d, caché escrita %d, salida %d",
        contexto.get() or "-", tipo or "otro", uso.get("input_tokens") or 0, uso.get("cache_read_input_tokens") or 0,
        uso.get("cache_creation_input_tokens") or 0, uso.get("output_tokens") or 0,
    )


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


async def complete(system: str | list[str], user: str, *, max_tokens: int = 200, variable: str = "",
                   tipo: str = "") -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens, variable)
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            r = await client.post(ENDPOINT, headers=headers, json=body)
            r.raise_for_status()
            data = r.json()
            _registrar_uso(data, tipo)
            return _text(data)
    except httpx.TimeoutException:
        log.warning("Claude no contestó en %ss: se usa la frase fija", settings.llm_timeout_seconds)
    except httpx.HTTPStatusError as exc:
        # El cuerpo del error de Anthropic dice el motivo (modelo, saldo, límite) y no lleva la clave.
        log.warning("Claude respondió %s: %s", exc.response.status_code, exc.response.text[:200])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Claude falló: %s", exc)
    return None


def complete_sync(system: str, user: str, *, max_tokens: int = 500, tipo: str = "") -> str | None:
    if not is_configured():
        return None
    headers, body = _request(system, user, max_tokens)
    try:
        r = httpx.post(ENDPOINT, headers=headers, json=body, timeout=settings.llm_analysis_timeout_seconds)
        r.raise_for_status()
        data = r.json()
        _registrar_uso(data, tipo)
        return _text(data)
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
