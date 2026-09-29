"""Dependencias compartidas: autenticación del panel y del canal interno."""

from __future__ import annotations

import base64
import secrets

from fastapi import Header, HTTPException, Request, status

from app import sso
from app.config import settings
from app.sso import Principal


class SesionRequerida(Exception):
    """No hay sesión y el panel está enmarcado: se responde con una página, no con un desafío.

    Un cuadro de usuario y contraseña dentro de un iframe cross-origin lo
    bloquean los navegadores, así que un 401 con `WWW-Authenticate` solo deja
    una pantalla en blanco. Acá se muestra qué hacer.
    """


# Secciones que el rol "calidad" no ve. Diagnóstico expone detalles de
# infraestructura (versiones, endpoints, estado de cada dependencia) que quien
# lee resultados de encuestas no necesita y un atacante sí querría.
RUTAS_SOLO_OPERACION = ("/health-detail",)


def require_internal_token(x_callbot_token: str = Header(default="")) -> None:
    """Protege /internal/*, que es donde el voice-agent escribe resultados.

    Sin esto, cualquiera con acceso al puerto de la API podría inyectar
    respuestas de encuesta o cerrar llamadas ajenas.
    """
    if not secrets.compare_digest(
        x_callbot_token.encode(), settings.internal_token.encode()
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token interno inválido",
        )


def _credencial_basic(request: Request) -> Principal | None:
    """HTTP Basic con la credencial de operación (ADMIN_USER/ADMIN_PASSWORD).

    Se parsea a mano en vez de usar `HTTPBasic()`: ese siempre responde 401 con
    desafío cuando falta el encabezado, y acá hay que poder decidir otra cosa.
    Se compara en bytes: `compare_digest` sobre `str` revienta con TypeError si
    aparece un carácter no ASCII, y eso convertiría una contraseña con "ñ" en un
    error 500 en vez de un 401.
    """
    esquema, _, dato = request.headers.get("authorization", "").partition(" ")
    if esquema.lower() != "basic" or not dato:
        return None
    try:
        usuario, _, clave = base64.b64decode(dato, validate=True).decode("utf-8").partition(":")
    except (ValueError, UnicodeDecodeError):
        return None
    usuario_ok = secrets.compare_digest(usuario.encode(), settings.admin_user.encode())
    clave_ok = secrets.compare_digest(clave.encode(), settings.admin_password.encode())
    return Principal(usuario, "admin", "basic") if usuario_ok and clave_ok else None


def require_admin(request: Request) -> Principal:
    """Deja pasar a quien tenga sesión del CRM o la credencial de operación.

    Primero la sesión: es lo que usa el personal de calidad. Basic queda para
    quien administra el sistema y entra directo, sin pasar por el CRM.
    """
    principal = sso.leer_sesion(request.cookies.get(sso.COOKIE)) or _credencial_basic(request)

    if principal is None:
        if request.headers.get("sec-fetch-dest") == "iframe":
            raise SesionRequerida()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )

    if principal.rol == "calidad" and request.url.path.startswith(RUTAS_SOLO_OPERACION):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta sección es solo para quien opera el sistema",
        )

    # Las plantillas lo leen para mostrar quién está y ocultar lo que no le toca.
    request.state.principal = principal
    return principal
