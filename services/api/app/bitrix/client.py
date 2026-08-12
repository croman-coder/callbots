"""Cliente REST de Bitrix24 vía webhook entrante.

Bitrix devuelve formas distintas según el método:
  crm.deal.list  -> {"result": [ {...}, ... ], "next": 50, "total": 120}
  crm.item.list  -> {"result": {"items": [ {...}, ... ]}, "next": 50, "total": 120}

`call_list` normaliza ambos casos y pagina hasta el final.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

log = logging.getLogger(__name__)

PAGE_SIZE = 50


class BitrixError(RuntimeError):
    """Error devuelto por la API de Bitrix o de transporte."""


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def normalize_phone_py(raw: str | None) -> str | None:
    """Normaliza un teléfono paraguayo a E.164 (+595...).

    Ejemplos:
        '0981 123 456'   -> '+595981123456'
        '595981123456'   -> '+595981123456'
        '+595 21 123456' -> '+59521123456'
        '021-123456'     -> '+59521123456'
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d+]", "", raw.strip())
    if not digits:
        return None

    if digits.startswith("+"):
        return digits if len(digits) >= 9 else None

    if digits.startswith("00"):
        digits = digits[2:]

    if digits.startswith("595"):
        return "+" + digits

    # Formato nacional: 0XXX...
    if digits.startswith("0"):
        return "+595" + digits[1:]

    # Sin prefijo ni cero inicial: asumimos número nacional
    if 8 <= len(digits) <= 10:
        return "+595" + digits

    log.warning("No se pudo normalizar el teléfono %r", raw)
    return None


def parse_bitrix_datetime(value: Any) -> datetime | None:
    """Parsea las fechas que devuelve Bitrix.

    Formatos vistos: '2026-08-12T00:00:00+03:00', '2026-08-12T00:00:00',
    '2026-08-12', y '' / None cuando el campo está vacío.
    """
    if not value or not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            log.warning("Fecha de Bitrix no reconocida: %r", value)
            return None

    # Sin tzinfo -> asumimos la zona configurada del portal
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.timezone)
    return dt.astimezone(timezone.utc)


def extract_phone(record: dict[str, Any]) -> str | None:
    """Saca el primer teléfono útil de un contacto o de un item de CRM.

    Bitrix guarda los teléfonos como multicampo:
        "PHONE": [{"ID": "1", "VALUE": "0981...", "VALUE_TYPE": "MOBILE"}]
    Se prefiere el móvil, porque es el que contesta una encuesta.
    """
    phones = record.get("PHONE") or record.get("phone") or []
    if isinstance(phones, str):
        return normalize_phone_py(phones)
    if not isinstance(phones, list):
        return None

    mobile = [p for p in phones if str(p.get("VALUE_TYPE", "")).upper() == "MOBILE"]
    for candidate in (mobile or phones):
        normalized = normalize_phone_py(candidate.get("VALUE"))
        if normalized:
            return normalized
    return None


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
class BitrixClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or settings.bitrix_base).rstrip("/") + "/"
        if not self.base_url.startswith("http"):
            raise BitrixError(
                "BITRIX_WEBHOOK_URL no está configurado. "
                "Formato esperado: https://portal.bitrix24.com/rest/1/TOKEN/"
            )
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> BitrixClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- llamada base ------------------------------------------------------
    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Ejecuta un método REST y devuelve el contenido de `result`."""
        url = f"{self.base_url}{method}.json"
        response = self._client.post(url, json=params or {})

        # 503 = portal saturado, 429 = rate limit -> los reintenta tenacity
        if response.status_code in (429, 500, 502, 503, 504):
            response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise BitrixError(
                f"{method}: respuesta no-JSON (HTTP {response.status_code})"
            ) from exc

        if "error" in payload:
            raise BitrixError(
                f"{method}: {payload.get('error')} - "
                f"{payload.get('error_description', 'sin detalle')}"
            )

        return payload.get("result")

    def call_raw(self, method: str, params: dict[str, Any] | None = None) -> dict:
        """Igual que `call` pero devuelve el payload completo (con next/total)."""
        url = f"{self.base_url}{method}.json"
        response = self._client.post(url, json=params or {})
        payload = response.json()
        if "error" in payload:
            raise BitrixError(
                f"{method}: {payload.get('error')} - "
                f"{payload.get('error_description', 'sin detalle')}"
            )
        return payload

    def call_list(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 200,
    ) -> list[dict[str, Any]]:
        """Pagina un método *.list hasta agotar los resultados."""
        params = dict(params or {})
        items: list[dict[str, Any]] = []
        start = 0
        pages = 0

        while pages < max_pages:
            params["start"] = start
            payload = self.call_raw(method, params)
            result = payload.get("result")

            if isinstance(result, dict):          # crm.item.list
                batch = result.get("items", [])
            elif isinstance(result, list):        # crm.deal.list, crm.contact.list
                batch = result
            else:
                batch = []

            items.extend(batch)
            pages += 1

            next_start = payload.get("next")
            if next_start is None or not batch:
                break
            start = next_start

        if pages >= max_pages:
            log.warning(
                "%s cortó en %d páginas (%d registros). ¿Filtro demasiado amplio?",
                method,
                max_pages,
                len(items),
            )
        return items

    # -- CRM ---------------------------------------------------------------
    def list_items(
        self,
        entity_type_id: int,
        filter_: dict[str, Any] | None = None,
        select: list[str] | None = None,
        order: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Lista registros de una SPA o entidad estándar via crm.item.list."""
        return self.call_list(
            "crm.item.list",
            {
                "entityTypeId": entity_type_id,
                "filter": filter_ or {},
                "select": select or ["*"],
                "order": order or {"id": "ASC"},
            },
        )

    def get_item_fields(self, entity_type_id: int) -> dict[str, Any]:
        """Devuelve la definición de campos. Sirve para descubrir los códigos UF_."""
        result = self.call("crm.item.fields", {"entityTypeId": entity_type_id})
        return (result or {}).get("fields", {})

    def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        try:
            return self.call("crm.contact.get", {"id": contact_id})
        except BitrixError as exc:
            log.warning("No se pudo leer el contacto %s: %s", contact_id, exc)
            return None

    def get_contacts(self, contact_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Trae varios contactos de una vez (evita N llamadas REST)."""
        if not contact_ids:
            return {}
        rows = self.call_list(
            "crm.contact.list",
            {
                "filter": {"@ID": contact_ids},
                "select": ["ID", "NAME", "LAST_NAME", "SECOND_NAME", "PHONE"],
            },
        )
        return {int(r["ID"]): r for r in rows}

    def update_item(
        self, entity_type_id: int, item_id: int, fields: dict[str, Any]
    ) -> Any:
        return self.call(
            "crm.item.update",
            {"entityTypeId": entity_type_id, "id": item_id, "fields": fields},
        )

    def add_timeline_comment(
        self, entity_type_id: int, entity_id: int, comment: str
    ) -> Any:
        """Deja un comentario en el timeline del registro.

        crm.timeline.comment.add usa ENTITY_TYPE en texto para las entidades
        clásicas; para SPA se usa 'dynamic_NNN'.
        """
        entity_type = {
            1: "lead",
            2: "deal",
            3: "contact",
            4: "company",
        }.get(entity_type_id, f"dynamic_{entity_type_id}")

        return self.call(
            "crm.timeline.comment.add",
            {
                "fields": {
                    "ENTITY_ID": entity_id,
                    "ENTITY_TYPE": entity_type,
                    "COMMENT": comment,
                }
            },
        )

    # -- Telefonía ---------------------------------------------------------
    def register_call(
        self,
        user_id: int,
        phone_number: str,
        call_start_date: datetime,
        crm_entity_type: str | None = None,
        crm_entity_id: int | None = None,
    ) -> str | None:
        """Registra una llamada saliente. Devuelve CALL_ID para cerrarla luego."""
        params: dict[str, Any] = {
            "USER_ID": user_id,
            "PHONE_NUMBER": phone_number,
            "TYPE": 1,  # 1 = saliente
            "CALL_START_DATE": call_start_date.isoformat(),
            "CRM_CREATE": 0,
            "SHOW": 0,
        }
        if crm_entity_type and crm_entity_id:
            params["CRM_ENTITY_TYPE"] = crm_entity_type
            params["CRM_ENTITY_ID"] = crm_entity_id

        try:
            result = self.call("telephony.externalcall.register", params)
            return (result or {}).get("CALL_ID")
        except BitrixError as exc:
            log.warning("No se pudo registrar la llamada en Bitrix: %s", exc)
            return None

    def finish_call(
        self,
        call_id: str,
        user_id: int,
        duration: int,
        status_code: str = "200",
        failed_reason: str | None = None,
    ) -> None:
        """Cierra la llamada registrada. status_code 200 = atendida."""
        params: dict[str, Any] = {
            "CALL_ID": call_id,
            "USER_ID": user_id,
            "DURATION": duration,
            "STATUS_CODE": status_code,
        }
        if failed_reason:
            params["FAILED_REASON"] = failed_reason
        try:
            self.call("telephony.externalcall.finish", params)
        except BitrixError as exc:
            log.warning("No se pudo cerrar la llamada %s en Bitrix: %s", call_id, exc)
