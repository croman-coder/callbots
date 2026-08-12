"""Dependencias compartidas: autenticación del panel y del canal interno."""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

basic_auth = HTTPBasic()


def require_internal_token(x_callbot_token: str = Header(default="")) -> None:
    """Protege /internal/*, que es donde el voice-agent escribe resultados.

    Sin esto, cualquiera con acceso al puerto de la API podría inyectar
    respuestas de encuesta o cerrar llamadas ajenas.
    """
    if not secrets.compare_digest(x_callbot_token, settings.internal_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token interno inválido",
        )


def require_admin(
    credentials: HTTPBasicCredentials = Depends(basic_auth),
) -> str:
    """HTTP Basic para el panel. compare_digest evita timing attacks."""
    user_ok = secrets.compare_digest(credentials.username, settings.admin_user)
    pass_ok = secrets.compare_digest(credentials.password, settings.admin_password)

    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
