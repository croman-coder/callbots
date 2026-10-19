"""Login con la cuenta de los dashboards.

Los tableros de Meta Ads y Bitrix comparten un proyecto Supabase: una persona,
una cuenta. Acá se reusa en vez de crear un padrón nuevo. El correo y la clave
se verifican contra GoTrue; el rol sale de la tabla `profiles`, leída con el JWT
del propio usuario para que el RLS de Supabase sea quien garantice que solo ve
su fila (así este módulo no puede volverse un lector del padrón por un error de
filtro, y no necesita ninguna clave de servicio).

Falla cerrado en todo: sin respuesta del padrón, sin fila, con un rol que no
está en `LOGIN_ROLES` o con un mapa mal escrito, no entra nadie.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

import httpx

from app.config import settings
from app.sso import ROLES_PERMITIDOS, Principal

log = logging.getLogger(__name__)

VIA = "dashboards"
_TIMEOUT = 8.0

# Frena el adivinar claves: 5 fallos en 10 minutos por correo y por origen.
_MAX_FALLOS = 5
_VENTANA = 600
_fallos: dict[str, deque[float]] = defaultdict(deque)
_cerrojo = threading.Lock()


class LoginRechazado(Exception):
    """El motivo es apto para mostrarse a quien intentó entrar."""

    def __init__(self, motivo: str, *, transitorio: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.transitorio = transitorio


def habilitado() -> bool:
    return bool(settings.supabase_url.strip() and settings.supabase_anon_key.strip())


def _reiniciar_intentos() -> None:
    with _cerrojo:
        _fallos.clear()


def _claves_de_bloqueo(email: str, origen: str) -> list[str]:
    return [f"correo:{email}"] + ([f"origen:{origen}"] if origen else [])


def _bloqueado(claves: list[str]) -> bool:
    ahora = time.monotonic()
    with _cerrojo:
        for k in claves:
            q = _fallos[k]
            while q and ahora - q[0] > _VENTANA:
                q.popleft()
            if len(q) >= _MAX_FALLOS:
                return True
    return False


def _anotar_fallo(claves: list[str]) -> None:
    ahora = time.monotonic()
    with _cerrojo:
        for k in claves:
            _fallos[k].append(ahora)


def _mapa_de_roles() -> dict[str, str]:
    """`super_admin:admin,manager:calidad` → {...}. Un mapa con un rol de Callbot
    desconocido o un par mal formado se descarta entero: mejor nadie que alguien de más."""
    mapa: dict[str, str] = {}
    for par in settings.login_roles.split(","):
        origen, sep, destino = par.strip().partition(":")
        if not sep or not origen.strip() or destino.strip() not in ROLES_PERMITIDOS:
            log.error("LOGIN_ROLES inválido (%r): el login con cuenta de dashboards queda cerrado", par)
            return {}
        mapa[origen.strip()] = destino.strip()
    return mapa


def _dominio_permitido(email: str) -> bool:
    _, _, dominio = email.rpartition("@")
    permitidos = {d.strip().lower() for d in settings.login_dominios.split(",") if d.strip()}
    return bool(dominio) and dominio in permitidos


def iniciar_sesion(
    email: str, clave: str, *, cliente: httpx.Client | None = None, origen: str = ""
) -> Principal:
    email = email.strip().lower()
    if not email or not clave:
        raise LoginRechazado("Escribí tu correo y tu clave.")
    if not _dominio_permitido(email):
        raise LoginRechazado("Tu cuenta no es del dominio de la empresa.")

    claves = _claves_de_bloqueo(email, origen)
    if _bloqueado(claves):
        raise LoginRechazado("Demasiados intentos. Esperá unos minutos y probá de nuevo.")

    propio = cliente is None
    http = cliente or httpx.Client(timeout=_TIMEOUT)
    try:
        base = settings.supabase_url.rstrip("/")
        cabecera = {"apikey": settings.supabase_anon_key}
        try:
            r = http.post(
                f"{base}/auth/v1/token",
                params={"grant_type": "password"},
                json={"email": email, "password": clave},
                headers=cabecera,
            )
        except httpx.HTTPError:
            log.warning("Login: no se pudo hablar con Supabase")
            raise LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)

        if r.status_code in (400, 401, 422):
            _anotar_fallo(claves)
            raise LoginRechazado("Correo o clave incorrectos.")
        if r.status_code != 200:
            log.warning("Login: Supabase respondió %s", r.status_code)
            raise LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)

        datos = r.json()
        token, uid = datos.get("access_token"), (datos.get("user") or {}).get("id")
        if not token or not uid:
            raise LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)

        try:
            p = http.get(
                f"{base}/rest/v1/profiles",
                params={"select": "role", "id": f"eq.{uid}"},
                headers={**cabecera, "Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError:
            raise LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)
        if p.status_code != 200:
            raise LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)

        filas = p.json()
        rol_dashboards = filas[0].get("role") if isinstance(filas, list) and filas else None
        rol = _mapa_de_roles().get(rol_dashboards or "")
        if rol is None:
            log.info("Login: %s (rol %r) sin acceso a Callbot", email, rol_dashboards)
            raise LoginRechazado("Tu cuenta no tiene acceso a Callbot. Pedilo a gerencia.")

        with _cerrojo:  # un ingreso correcto borra los fallos previos de esa cuenta
            _fallos.pop(f"correo:{email}", None)
        log.info("Login: %s entró como %s", email, rol)
        return Principal(email, rol, VIA)
    finally:
        if propio:
            http.close()
