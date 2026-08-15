"""Tapa los secretos que llegan al log.

El caso que motivó esto: httpx registra cada request en INFO, con la URL
completa. La autenticación de un webhook de Bitrix vive **entera en la URL**
—`/rest/<user>/<token>/metodo.json`—, así que cada llamada al CRM dejaba el
token en texto plano en los logs del contenedor. Cualquiera con acceso a
`docker logs` o al panel de Coolify se llevaba acceso completo al CRM.

Un filtro sobre el logger raíz es la única forma de cubrirlo de verdad: silenciar
httpx taparía este caso y dejaría abierto el próximo. Acá se redacta el valor
mire por donde mire, venga de la librería que venga.
"""

from __future__ import annotations

import logging

from app.config import settings

# Debajo de esto no se redacta: un secreto de dos caracteres haría un
# reemplazo masivo sobre texto legítimo y dejaría el log ilegible.
_LARGO_MINIMO = 8
_MARCA = "***"


def _token_del_webhook(url: str) -> str | None:
    """Saca el token de una URL de webhook entrante de Bitrix.

    Formato: https://portal.bitrix24.es/rest/<user_id>/<token>/
    """
    partes = [p for p in url.split("/") if p]
    if "rest" not in partes:
        return None
    i = partes.index("rest")
    # user_id va en i+1, el token en i+2
    return partes[i + 2] if len(partes) > i + 2 else None


def _secretos() -> list[str]:
    candidatos = [
        _token_del_webhook(settings.bitrix_webhook_url),
        settings.internal_token,
        settings.admin_password,
        settings.ari_password,
        settings.gemini_api_key,
    ]
    # Sin duplicados y de más largo a más corto: si un secreto contiene a otro,
    # se redacta primero el largo para no dejar un pedazo suelto.
    unicos = {c for c in candidatos if c and len(c) >= _LARGO_MINIMO}
    return sorted(unicos, key=len, reverse=True)


class RedactarSecretos(logging.Filter):
    """Reemplaza cualquier secreto configurado por `***` antes de emitir."""

    def __init__(self) -> None:
        super().__init__()
        self._secretos = _secretos()

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        if not self._secretos:
            return True

        try:
            mensaje = record.getMessage()
        except Exception:  # noqa: BLE001 - un log mal formado no debe tirar la app
            return True

        limpio = mensaje
        for secreto in self._secretos:
            if secreto in limpio:
                limpio = limpio.replace(secreto, _MARCA)

        if limpio != mensaje:
            # Se pisa el mensaje ya interpolado: dejar los args haría que el
            # handler los volviera a insertar y el secreto reaparecería.
            record.msg = limpio
            record.args = ()

        return True


def instalar(logger: logging.Logger | None = None) -> None:
    """Cuelga el filtro de un logger y de sus handlers. Por defecto, el raíz.

    Va en los dos lados: el logger cubre lo que se propaga hacia arriba, y los
    handlers cubren a los que tienen `propagate = False`.

    Es idempotente: Celery reconfigura el logging al arrancar el worker, así que
    esto se llama más de una vez y no debe apilar filtros repetidos.
    """
    destino = logger or logging.getLogger()

    if not any(isinstance(f, RedactarSecretos) for f in destino.filters):
        destino.addFilter(RedactarSecretos())

    for handler in destino.handlers:
        if not any(isinstance(f, RedactarSecretos) for f in handler.filters):
            handler.addFilter(RedactarSecretos())
