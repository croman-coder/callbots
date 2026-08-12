"""Control de llamadas vía ARI (Asterisk REST Interface).

Solo necesitamos originar y colgar: el audio lo maneja el dialplan, que
entrega el canal a AudioSocket contra el servicio voice-agent.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class AriError(RuntimeError):
    pass


class AriClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self.base = settings.ari_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            auth=(settings.ari_user, settings.ari_password),
        )

    def __enter__(self) -> AriClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self.base}/ari{path}"
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise AriError(f"No se pudo contactar Asterisk en {url}: {exc}") from exc

        if response.status_code >= 400:
            raise AriError(
                f"ARI {method} {path} -> HTTP {response.status_code}: {response.text[:300]}"
            )
        return response

    # ------------------------------------------------------------------
    def ping(self) -> dict[str, Any]:
        """Verifica que Asterisk esté vivo y las credenciales ARI sean válidas."""
        return self._request("GET", "/asterisk/info").json()

    def build_endpoint(self, phone: str) -> str:
        """Arma el endpoint a marcar a partir de la plantilla configurada."""
        number = phone.lstrip("+")
        template = settings.asterisk_dial_template
        if "{number}" in template:
            return template.format(number=number)
        # Modo softphone: la plantilla es un endpoint fijo y el número se ignora
        return template

    def originate(
        self,
        phone: str,
        session_uuid: uuid.UUID,
        caller_id: str | None = None,
        timeout: int | None = None,
        extra_variables: dict[str, str] | None = None,
    ) -> str:
        """Lanza la llamada. Devuelve el channel id de Asterisk.

        El canal entra al dialplan en `callbot-outbound,start,1` recién cuando
        el destino atiende; ahí el dialplan lo enchufa a AudioSocket usando
        SESSION_UUID para que el voice-agent sepa qué encuesta correr.
        """
        endpoint = self.build_endpoint(phone)

        variables = {
            "SESSION_UUID": str(session_uuid),
            "CALLBOT_PHONE": phone,
        }
        if extra_variables:
            variables.update(extra_variables)

        body = {
            "endpoint": endpoint,
            "context": settings.asterisk_context,
            "extension": settings.asterisk_extension,
            "priority": 1,
            "callerId": caller_id or settings.asterisk_callerid,
            "timeout": timeout or settings.asterisk_dial_timeout,
            "variables": variables,
        }

        log.info(
            "ARI originate -> endpoint=%s sesion=%s destino=%s",
            endpoint, session_uuid, phone,
        )
        response = self._request("POST", "/channels", json=body)
        channel = response.json()
        channel_id = channel.get("id")
        if not channel_id:
            raise AriError(f"Originate sin channel id en la respuesta: {channel}")
        return channel_id

    def hangup(self, channel_id: str, reason: str = "normal") -> None:
        try:
            self._request("DELETE", f"/channels/{channel_id}", params={"reason": reason})
        except AriError as exc:
            # 404 es lo normal si el canal ya se cerró solo
            log.debug("Hangup de %s sin efecto: %s", channel_id, exc)

    def channel_state(self, channel_id: str) -> str | None:
        try:
            return self._request("GET", f"/channels/{channel_id}").json().get("state")
        except AriError:
            return None
