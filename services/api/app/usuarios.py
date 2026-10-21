"""Usuarios de Callbot: son los de los dashboards.

Una persona, una cuenta: el padrón es el Supabase que comparten los tableros de Meta Ads y Bitrix (ver
app/cuentas.py). Acá no hay un padrón nuevo ni una clave de servicio: la pantalla Usuarios le habla a la API de
administración de los dashboards (`/api/admin-user`) con el token de Supabase de quien está administrando, y esa
API es la que exige que sea `super_admin`, valida todo y escribe. Así hay una sola lógica de altas, bajas y roles.

El token se obtiene al ingresar con la cuenta (o al confirmar la clave en la pantalla) y vive en una cookie corta,
httponly y solo para /usuarios: ninguna otra pantalla ni ningún script de la página la ve.

Lo que NO se hace desde acá a propósito: borrar usuarios. Borrar una cuenta la borra de todos los dashboards, y eso
se decide allá.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

import httpx
from fastapi import Response

from app import cuentas
from app.config import settings

log = logging.getLogger(__name__)

COOKIE_TOKEN = "callbot_sb"
RUTA = "/usuarios"
_TIMEOUT = 20.0

# Roles de los dashboards, con cómo se llaman y qué significan acá. Callbot solo deja entrar a los que figuran en
# LOGIN_ROLES (por defecto super_admin → Administrador y manager → Calidad).
ROLES = {
    "super_admin": ("Super admin", "Administra todo: dashboards, usuarios y Callbot."),
    "manager": ("Manager", "Ve los dashboards de sus marcas (de todas si no tiene ninguna asignada) y entra a Callbot como Calidad."),
    "brand": ("Marca", "Solo ve sus marcas en los dashboards. No entra a Callbot."),
}
# Las marcas de los dashboards (api/_usuarios.js, VALID_BRANDS). Si allá suman una, la API la rechaza hasta que
# se agregue acá; nunca se guarda una que el dashboard no conozca.
MARCAS = (
    "JETOUR", "GWM", "RENEW", "JAC", "SOUEAST", "LEAPMOTOR", "RENAULT", "MITSUBISHI", "JMEV", "ZEEKR", "XPENG",
)
ACCIONES = frozenset({"crear", "editar", "baja", "reactivar", "clave"})
MIN_CLAVE = 8

_CORREO = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


class UsuariosError(Exception):
    """El motivo se puede mostrar tal cual a quien administra."""

    def __init__(self, motivo: str, *, estado: int = 400, reautenticar: bool = False) -> None:
        super().__init__(motivo)
        self.motivo = motivo
        self.estado = estado
        self.reautenticar = reautenticar


# ─────────────────────────────────────────────────────────── cookie del token
def poner_cookie(respuesta: Response, token: str, vence: int) -> None:
    """Guarda el token de Supabase para /usuarios. Dura lo que el token, con un margen y un tope de 55 minutos."""
    if not token or vence <= 120:
        return
    respuesta.set_cookie(
        COOKIE_TOKEN, token, max_age=min(vence - 60, 3300),
        httponly=True, secure=True, samesite="strict", path=RUTA,
    )


def borrar_cookie(respuesta: Response) -> None:
    respuesta.delete_cookie(COOKIE_TOKEN, path=RUTA, secure=True, httponly=True, samesite="strict")


# ─────────────────────────────────────────────────────────── la API de los dashboards
def _url() -> str:
    return settings.dashboards_url.rstrip("/") + "/api/admin-user"


def _llamar(token: str, cuerpo: dict, *, cliente: httpx.Client | None = None) -> dict:
    propio = cliente is None
    http = cliente or httpx.Client(timeout=_TIMEOUT)
    try:
        try:
            r = http.post(_url(), json=cuerpo, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            log.warning("Usuarios: no se pudo hablar con los dashboards: %s", exc)
            raise UsuariosError("No se pudo hablar con los dashboards. Probá de nuevo en un momento.", estado=502) from exc
        try:
            datos = r.json()
        except ValueError:
            datos = {}
        if r.status_code == 401:
            raise UsuariosError("Venció tu sesión de administración. Confirmá tu clave para seguir.", estado=401, reautenticar=True)
        if r.status_code == 403:
            raise UsuariosError("Solo un super admin de los dashboards puede administrar usuarios.", estado=403)
        if r.status_code >= 400:
            motivo = str(datos.get("error") or "")[:200] if isinstance(datos, dict) else ""
            log.warning("Usuarios: los dashboards respondieron %s (%s)", r.status_code, motivo)
            raise UsuariosError(motivo or "Los dashboards no pudieron hacerlo. Probá de nuevo.", estado=502 if r.status_code >= 500 else 400)
        return datos if isinstance(datos, dict) else {}
    finally:
        if propio:
            http.close()


def acceso_a_callbot(rol_dashboards: str) -> str | None:
    """Con qué rol entra a Callbot alguien que tiene este rol en los dashboards, o None si no entra."""
    return cuentas._mapa_de_roles().get(rol_dashboards)


def _fecha(valor: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")) if valor else None
    except ValueError:
        return None


def _texto_sobre(color: str) -> str:
    """Blanco u oscuro, el que se lea sobre este color de fondo (luminancia relativa, WCAG)."""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return "#ffffff" if 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] < 0.4 else "#16181d"


def listar(token: str, *, cliente: httpx.Client | None = None) -> list[dict]:
    """Las personas con su rol, marcas, estado y cómo entran a Callbot. Primero las que tienen acceso."""
    crudo = _llamar(token, {"action": "list"}, cliente=cliente).get("users") or []
    salida = []
    for u in crudo:
        rol = str(u.get("role") or "brand")
        acceso = acceso_a_callbot(rol)
        nombre = str(u.get("display_name") or u.get("email") or "").strip()
        color = u.get("color") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(u.get("color") or "")) else "#1E2A78"
        salida.append({
            "id": str(u.get("id") or ""),
            "correo": str(u.get("email") or ""),
            "nombre": nombre,
            "inicial": (nombre[:1] or "?").upper(),
            "color": color,
            "texto_sobre_color": _texto_sobre(color),
            "rol": rol,
            "rol_nombre": ROLES.get(rol, (rol, ""))[0],
            "acceso": acceso,
            "acceso_nombre": {"admin": "Administrador", "calidad": "Calidad"}.get(acceso or "", "Sin acceso"),
            "marcas": list(u.get("brands") or []),
            "baja": bool(u.get("baja")),
            "ultimo_ingreso": _fecha(u.get("last_sign_in_at")),
            "creado": _fecha(u.get("created_at")),
        })
    salida.sort(key=lambda p: (p["baja"], p["acceso"] is None, p["nombre"].lower()))
    return salida


# ─────────────────────────────────────────────────────────── validación de lo que llega del panel
def _nombre(valor: object) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(valor or ""))).strip()[:60]


def _marcas(valor: object) -> list[str]:
    if not isinstance(valor, list):
        return []
    return [m for m in dict.fromkeys(valor) if m in MARCAS]


def _id(valor: object) -> str:
    texto = str(valor or "")
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", texto):
        raise UsuariosError("Esa persona no existe.")
    return texto


def ejecutar(token: str, yo: str, datos: dict, *, cliente: httpx.Client | None = None) -> str:
    """Valida un pedido del panel y lo manda a los dashboards. Devuelve el aviso para mostrar.

    `yo` es el correo de quien administra: nadie se cambia el rol, se da de baja ni se resetea la clave a sí mismo desde
    acá (para eso están el dashboard y «olvidé mi clave»).
    """
    accion = str(datos.get("accion") or "")
    if accion not in ACCIONES:
        raise UsuariosError("Esa acción no existe.")

    if accion == "crear":
        correo = str(datos.get("correo") or "").strip().lower()
        if not _CORREO.fullmatch(correo):
            raise UsuariosError("El correo no parece válido.")
        if not cuentas._dominio_permitido(correo):
            raise UsuariosError(f"El correo tiene que ser de {settings.login_dominios.split(',')[0].strip()}: con otro dominio no podría ingresar.")
        rol = str(datos.get("rol") or "")
        if rol not in ROLES:
            raise UsuariosError("Elegí un rol.")
        nombre = _nombre(datos.get("nombre")) or correo.split("@")[0]
        invitar = bool(datos.get("invitar", True))
        clave = str(datos.get("clave") or "")
        if not invitar and len(clave) < MIN_CLAVE:
            raise UsuariosError(f"La clave tiene que tener al menos {MIN_CLAVE} caracteres.")
        cuerpo = {"action": "create", "email": correo, "display_name": nombre, "role": rol,
                  "brands": _marcas(datos.get("marcas")), "invitar": invitar}
        if not invitar:
            cuerpo["password"] = clave
        r = _llamar(token, cuerpo, cliente=cliente)
        log.info("Usuarios: %s creó a %s (%s)", yo, correo, rol)
        inv = r.get("invitacion") or {}
        if invitar and isinstance(inv, dict) and inv.get("enviado") is False and not inv.get("delegado"):
            return f"Se creó a {correo}, pero no se pudo mandar la invitación. Que use «¿Olvidaste tu clave?» en el dashboard."
        return f"Se creó a {correo}." + (" Le llegó una invitación por correo para elegir su clave." if invitar else " Va a tener que cambiar la clave en su primer ingreso.")

    uid = _id(datos.get("id"))
    gente = listar(token, cliente=cliente)
    persona = next((p for p in gente if p["id"] == uid), None)
    if persona is None:
        raise UsuariosError("Esa persona ya no está. Actualizá la lista.")
    es_yo = persona["correo"].lower() == yo.lower()

    if accion == "editar":
        rol = str(datos.get("rol") or persona["rol"])
        if rol not in ROLES:
            raise UsuariosError("Elegí un rol.")
        if es_yo and rol != persona["rol"]:
            raise UsuariosError("No podés cambiar tu propio rol: pedíselo a otro super admin.")
        nombre = _nombre(datos.get("nombre")) or persona["nombre"]
        _llamar(token, {"action": "update", "user_id": uid, "display_name": nombre, "role": rol}, cliente=cliente)
        if "marcas" in datos:
            _llamar(token, {"action": "set_brands", "user_id": uid, "brands": _marcas(datos.get("marcas"))}, cliente=cliente)
        log.info("Usuarios: %s editó a %s (rol %s)", yo, persona["correo"], rol)
        return f"Se guardaron los cambios de {nombre}."

    if accion == "baja":
        if es_yo:
            raise UsuariosError("No podés darte de baja a vos mismo.")
        _llamar(token, {"action": "deactivate", "user_id": uid}, cliente=cliente)
        log.info("Usuarios: %s dio de baja a %s", yo, persona["correo"])
        return f"{persona['nombre']} quedó de baja: no puede ingresar a ningún dashboard ni a Callbot. Se puede reactivar."

    if accion == "reactivar":
        _llamar(token, {"action": "reactivate", "user_id": uid}, cliente=cliente)
        log.info("Usuarios: %s reactivó a %s", yo, persona["correo"])
        return f"{persona['nombre']} volvió a estar activo."

    # accion == "clave"
    if es_yo:
        raise UsuariosError("Tu propia clave se cambia desde el dashboard («¿Olvidaste tu clave?»).")
    clave = str(datos.get("clave") or "")
    if len(clave) < MIN_CLAVE:
        raise UsuariosError(f"La clave tiene que tener al menos {MIN_CLAVE} caracteres.")
    _llamar(token, {"action": "reset_password", "user_id": uid, "new_password": clave}, cliente=cliente)
    log.info("Usuarios: %s cambió la clave de %s", yo, persona["correo"])  # nunca la clave
    return f"Se cambió la clave de {persona['nombre']}. Va a tener que elegir otra en su próximo ingreso."
