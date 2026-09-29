"""Acceso desde el CRM: ticket de un solo uso y sesión firmada.

El panel vive embebido en el CRM (pestaña "Calidad") y solo debe abrirse para
quien el CRM ya autenticó y autorizó. El CRM emite un ticket firmado con un
secreto que comparte con Callbot; Callbot lo canjea una vez por una cookie de
sesión. No hay una segunda contraseña que recordar ni que se filtre.

    CRM (servidor)                       Callbot
    ──────────────                       ───────
    verifica JWT + rol en team_members
    firma ticket (60 s, un solo uso) ──► GET /sso?ticket=…
                                         verifica firma, vigencia, rol, jti
                                         Set-Cookie: callbot_session (HttpOnly)
                                    ◄──  303 → /

Decisiones que conviene no deshacer:

* **Una llave por propósito.** Ticket y sesión se firman con llaves derivadas
  distintas del mismo secreto (HMAC del nombre del propósito). Así una cookie de
  sesión robada no sirve como ticket ni un ticket como cookie.
* **El ticket es de un solo uso** (`jti` recordado hasta que vence). Vive en la
  URL, y las URLs quedan en logs e historial: canjeado una vez, el que quede en
  un log es inútil. Asume UN solo worker de uvicorn, que es como corre hoy; con
  varios habría que mover `_usados` a Redis.
* **Vigencia acotada del lado de Callbot.** Se rechaza un ticket cuya vida
  (`exp - iat`) supere el tope, aunque la firma sea válida: un CRM mal
  configurado o comprometido no puede emitir tickets de una semana.
* **Sin secreto (o menor a 32 caracteres) todo falla cerrado**: `/sso` no
  existe y ninguna cookie valida. Es el estado por defecto.
* **Matar todas las sesiones** = rotar `CALLBOT_SSO_SECRET`. No hay lista de
  sesiones que revocar una por una.

Este módulo no importa `app.config` al cargarse, para poder probarlo sin
levantar toda la aplicación.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

# Roles del CRM que pueden entrar. "calidad" es el que se agrega para este panel;
# "owner" y "admin" ya existen y ven todo el CRM, así que también ven esto.
ROLES_PERMITIDOS = frozenset({"calidad", "admin", "owner"})

COOKIE = "callbot_session"
TTL_TICKET = 60          # segundos que puede vivir un ticket, como el del simulador
TTL_SESION = 8 * 3600    # una jornada; se renueva sola al reabrir la pestaña en el CRM
_TOLERANCIA_RELOJ = 5    # segundos de desfase aceptados entre el CRM y este servidor
_MIN_SECRETO = 32

_usados: dict[str, float] = {}   # jti → vencimiento


@dataclass(frozen=True)
class Principal:
    """Quién está usando el panel y por qué vía entró."""

    usuario: str
    rol: str
    via: str  # "crm" (ticket/sesión) o "basic" (credencial de operación)


# ---------------------------------------------------------------- primitivas
def _secreto() -> str:
    from app.config import settings  # import tardío: ver docstring del módulo

    return settings.sso_secret


def habilitado(secreto: str | None = None) -> bool:
    return len(secreto if secreto is not None else _secreto()) >= _MIN_SECRETO


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def _llave(secreto: str, proposito: str) -> bytes:
    return hmac.new(secreto.encode(), proposito.encode(), hashlib.sha256).digest()


def firmar(proposito: str, payload: dict, secreto: str | None = None) -> str:
    secreto = _secreto() if secreto is None else secreto
    if not habilitado(secreto):
        raise RuntimeError("SSO sin secreto: no se puede firmar")
    cuerpo = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    firma = hmac.new(
        _llave(secreto, proposito), f"{proposito}.{cuerpo}".encode(), hashlib.sha256
    ).digest()
    return f"{proposito}.{cuerpo}.{_b64(firma)}"


def abrir(proposito: str, token: str, secreto: str | None = None) -> dict | None:
    """Devuelve el payload si la firma y el propósito coinciden; si no, None."""
    secreto = _secreto() if secreto is None else secreto
    if not habilitado(secreto) or not isinstance(token, str):
        return None
    partes = token.split(".")
    if len(partes) != 3 or partes[0] != proposito:
        return None
    esperada = _b64(
        hmac.new(
            _llave(secreto, proposito), f"{partes[0]}.{partes[1]}".encode(), hashlib.sha256
        ).digest()
    )
    # compare_digest: comparación en tiempo constante, no filtra cuántos
    # caracteres de la firma coincidieron.
    if not hmac.compare_digest(esperada, partes[2]):
        return None
    try:
        payload = json.loads(_unb64(partes[1]))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# -------------------------------------------------------------------- ticket
def _purgar(ahora: float) -> None:
    for jti in [j for j, vence in _usados.items() if vence < ahora]:
        _usados.pop(jti, None)


def canjear_ticket(
    ticket: str, secreto: str | None = None, ahora: float | None = None
) -> Principal | None:
    """Valida un ticket del CRM y lo consume. None si algo no cierra."""
    payload = abrir("t1", ticket, secreto)
    if payload is None:
        return None
    ahora = time.time() if ahora is None else ahora

    iat, exp = payload.get("iat"), payload.get("exp")
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (iat, exp)):
        return None
    if exp < ahora:
        return None
    if iat > ahora + _TOLERANCIA_RELOJ:
        return None
    if exp - iat > TTL_TICKET + _TOLERANCIA_RELOJ:
        return None

    rol, sub, jti = payload.get("rol"), payload.get("sub"), payload.get("jti")
    if rol not in ROLES_PERMITIDOS or not sub or not jti:
        return None

    _purgar(ahora)
    if jti in _usados:
        return None
    _usados[str(jti)] = float(exp)

    return Principal(usuario=str(payload.get("email") or sub), rol=rol, via="crm")


# -------------------------------------------------------------------- sesión
def emitir_sesion(
    principal: Principal, secreto: str | None = None, ahora: float | None = None
) -> str:
    ahora = time.time() if ahora is None else ahora
    return firmar(
        "s1",
        {
            "u": principal.usuario,
            "rol": principal.rol,
            "iat": int(ahora),
            "exp": int(ahora + TTL_SESION),
        },
        secreto,
    )


def leer_sesion(
    token: str | None, secreto: str | None = None, ahora: float | None = None
) -> Principal | None:
    if not token:
        return None
    payload = abrir("s1", token, secreto)
    if payload is None:
        return None
    ahora = time.time() if ahora is None else ahora
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool) or exp < ahora:
        return None
    rol, usuario = payload.get("rol"), payload.get("u")
    # El rol se revalida al leer: si mañana se quita "calidad" de la lista, las
    # sesiones viejas con ese rol dejan de valer sin esperar a que venzan.
    if rol not in ROLES_PERMITIDOS or not usuario:
        return None
    return Principal(usuario=str(usuario), rol=rol, via="crm")
