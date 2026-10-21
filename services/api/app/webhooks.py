"""Webhooks de resultados: después de cada llamada, un JSON con todo lo que pasó a las URLs que cargue el agente.

Sirve para mandar los resultados a otro sistema (n8n, Make, una planilla, un CRM) sin tocar Callbot. Es el «After-call
destinations → webhook» de Dapta, con la firma que usa cualquier integración seria:

- `X-Callbot-Firma: sha256=<hex>`: HMAC-SHA256 del cuerpo con el secreto del agente. Quien recibe recalcula y compara;
  así sabe que lo mandó Callbot y que nadie lo cambió en el camino.
- `X-Callbot-Evento`: `llamada_terminada` o `prueba`.

Solo direcciones públicas, igual que «Generar contexto»: la API vive en la red interna de Docker y una URL como
`http://postgres:5432` haría que el servidor le pegue a sus propios servicios. Cada salto se valida y se conecta a la
IP ya validada. Cada envío queda registrado (estado, error, tiempo).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app import agentes
from app.datos_cliente import valores as valores_cliente
from app.services import variables
from app.services.scoring import to_scale_10

log = logging.getLogger(__name__)

MAX_WEBHOOKS = 10
TIMEOUT = 10.0
EVENTO = "llamada_terminada"


class WebhookInvalido(ValueError):
    """El motivo se puede mostrar tal cual."""


def nuevo_secreto() -> str:
    return secrets.token_hex(24)


def validar_url(url: str) -> str:
    """La URL normalizada, si es https/http a un host público. Levanta WebhookInvalido."""
    try:
        url = agentes.normalizar_url(url)
        agentes.resolver_publico(urlparse(url).hostname or "")
    except agentes.SitioInvalido as exc:
        raise WebhookInvalido(str(exc)) from exc
    if len(url) > 500:
        raise WebhookInvalido("La dirección es demasiado larga.")
    return url


def _iso(momento: datetime | None) -> str | None:
    if not momento:
        return None
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento.isoformat()


def payload(call, campaign) -> dict[str, Any]:  # noqa: ANN001 - CallAttempt / Campaign
    """Todo lo que pasó en la llamada, en un JSON estable para integrar."""
    target = call.target
    analisis = call.analysis
    respuestas = []
    for a in sorted(call.answers, key=lambda a: a.question.position):
        q = a.question
        puntaje = to_scale_10(a.value_numeric, q.qtype) if a.value_numeric is not None else None
        respuestas.append({
            "posicion": q.position,
            "pregunta": q.text,
            "transcripcion": a.transcript,
            "valor": a.value_numeric,
            "puntaje_10": puntaje,
        })
    datos = valores_cliente(target, campaign) if target is not None else {}
    return {
        "evento": EVENTO,
        "agente": {"id": campaign.id, "nombre": campaign.name},
        "llamada": {
            "id": call.id,
            "resultado": call.outcome.value if call.outcome else None,
            "inicio": _iso(call.started_at),
            "atendida": _iso(call.answered_at),
            "fin": _iso(call.ended_at),
            "duracion_segundos": call.duration_seconds,
            "intento": call.attempt_number,
            "causa_corte": call.hangup_cause,
            "preguntas_hechas": call.questions_asked,
            "preguntas_respondidas": call.questions_answered,
        },
        "cliente": {
            "nombre": target.contact_name if target else None,
            "telefono": target.phone if target else None,
            "datos": {k: v for k, v in datos.items() if k not in ("empresa",)},
            "bitrix": {
                "entity_type_id": target.bitrix_entity_type_id if target else None,
                "entity_id": target.bitrix_entity_id if target else None,
            },
        },
        "respuestas": respuestas,
        "analisis": None if analisis is None else {
            "satisfaccion": analisis.satisfaction_score,
            "sentimiento": analisis.sentiment,
            "resumen": analisis.summary,
            "temas": analisis.topics or [],
            "requiere_seguimiento": bool(analisis.requires_followup),
            "motivo_seguimiento": analisis.followup_reason,
            "variables": dict(variables.pares(campaign.analysis_fields, analisis.extras)),
        },
    }


def ejemplo(campaign) -> dict[str, Any]:  # noqa: ANN001
    """Un envío de muestra para probar cuando el agente todavía no hizo ninguna llamada."""
    return {
        "evento": "prueba",
        "agente": {"id": campaign.id, "nombre": campaign.name},
        "llamada": {"id": 0, "resultado": "completed", "inicio": datetime.now(timezone.utc).isoformat(),
                    "duracion_segundos": 75, "intento": 1, "causa_corte": None,
                    "preguntas_hechas": 1, "preguntas_respondidas": 1},
        "cliente": {"nombre": "Cliente de prueba", "telefono": "+595981000000", "datos": {"modelo": "Kardian"},
                    "bitrix": {"entity_type_id": None, "entity_id": None}},
        "respuestas": [{"posicion": 1, "pregunta": "Del cero al diez, ¿qué tan satisfecho quedó?",
                        "transcripcion": "un nueve", "valor": 9, "puntaje_10": 9}],
        "analisis": {"satisfaccion": 9.0, "sentimiento": "positivo", "resumen": "Envío de prueba.", "temas": [],
                     "requiere_seguimiento": False, "motivo_seguimiento": None, "variables": {}},
    }


def firma(secreto: str, cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def _post(url: str, cuerpo: bytes, cabeceras: dict[str, str]) -> int:
    """POST conectando a la IP pública ya validada (también en cada redirección). Devuelve el código HTTP."""
    for _ in range(agentes.MAX_REDIRECCIONES + 1):
        p = urlparse(url)
        ip = agentes.resolver_publico(p.hostname or "")
        puerto = f":{p.port}" if p.port else ""
        destino = p._replace(netloc=f"[{ip}]{puerto}" if ":" in ip else f"{ip}{puerto}").geturl()
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as cliente:
            r = cliente.post(destino, content=cuerpo, headers={**cabeceras, "Host": p.netloc.split("@")[-1]},
                             extensions={"sni_hostname": p.hostname})
        if r.is_redirect and r.status_code in (307, 308):
            url = httpx.URL(url).join(r.headers.get("location", "")).__str__()
            if urlparse(url).scheme not in ("http", "https"):
                raise WebhookInvalido("Redirige a una dirección que no se puede usar.")
            continue
        return r.status_code
    raise WebhookInvalido("Redirige demasiadas veces.")


def enviar(url: str, datos: dict[str, Any], secreto: str) -> tuple[int | None, str | None, int]:
    """Manda el JSON firmado. Devuelve (código HTTP o None, error legible o None, milisegundos). Nunca levanta."""
    cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode()
    cabeceras = {
        "Content-Type": "application/json",
        "User-Agent": "Callbot/1.0",
        "X-Callbot-Evento": str(datos.get("evento") or EVENTO),
        "X-Callbot-Firma": firma(secreto, cuerpo),
    }
    inicio = time.monotonic()
    try:
        estado = _post(url, cuerpo, cabeceras)
        error = None if 200 <= estado < 300 else f"Respondió {estado}"
    except (agentes.SitioInvalido, WebhookInvalido) as exc:
        estado, error = None, str(exc)
    except httpx.TimeoutException:
        estado, error = None, f"No respondió en {TIMEOUT:.0f} s"
    except httpx.HTTPError as exc:
        estado, error = None, f"No se pudo conectar ({exc.__class__.__name__})"
    ms = int((time.monotonic() - inicio) * 1000)
    if error:
        log.warning("Webhook %s: %s", urlparse(url).hostname, error)
    return estado, error, ms
