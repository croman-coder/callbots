"""Login con la cuenta de los dashboards (Supabase). Sin red: el cliente HTTP es un transporte simulado."""
from __future__ import annotations

import json

import httpx
import pytest

from app import cuentas
from app.cuentas import LoginRechazado

URL = "https://dashboards.test/supabase"
ANON = "anon-key-de-prueba"


def _cliente(*, login=200, perfil=None, marcas=None, caido=False, vistos=None):
    """Simula GoTrue + PostgREST. `perfil=None` = sin fila en profiles."""

    def manejar(req: httpx.Request) -> httpx.Response:
        if vistos is not None:
            vistos.append(req)
        if caido:
            raise httpx.ConnectError("sin red")
        ruta = req.url.path
        if ruta.endswith("/auth/v1/token"):
            if login != 200:
                return httpx.Response(400, json={"error_code": "invalid_credentials"})
            return httpx.Response(200, json={"access_token": "jwt-del-usuario", "user": {"id": "u-1", "email": "ana@santarosa.com.py"}})
        if ruta.endswith("/rest/v1/profiles"):
            return httpx.Response(200, json=[] if perfil is None else [{"role": perfil}])
        if ruta.endswith("/rest/v1/user_brands"):
            return httpx.Response(200, json=marcas or [])
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(manejar))


@pytest.fixture(autouse=True)
def _configuracion(monkeypatch):
    monkeypatch.setattr(cuentas.settings, "supabase_url", URL)
    monkeypatch.setattr(cuentas.settings, "supabase_anon_key", ANON)
    monkeypatch.setattr(cuentas.settings, "login_dominios", "santarosa.com.py")
    monkeypatch.setattr(cuentas.settings, "login_roles", "super_admin:admin,manager:calidad")
    cuentas._reiniciar_intentos()


def test_super_admin_entra_como_admin():
    p = cuentas.iniciar_sesion("Ana@SantaRosa.com.py", "clave-larga-1", cliente=_cliente(perfil="super_admin"))
    assert (p.usuario, p.rol, p.via) == ("ana@santarosa.com.py", "admin", "dashboards")


def test_manager_entra_como_calidad():
    p = cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil="manager"))
    assert p.rol == "calidad"


@pytest.mark.parametrize("rol", ["brand", "administracion", "inventado"])
def test_roles_sin_acceso_a_encuestas_no_entran(rol):
    # Las encuestas tienen teléfonos de clientes: por defecto solo gerencia y administración del sistema.
    with pytest.raises(LoginRechazado, match="no tiene acceso"):
        cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil=rol))


def test_cuenta_sin_fila_en_el_padron_no_entra():
    # El Supabase acepta registros públicos: tener sesión no es estar en el padrón.
    with pytest.raises(LoginRechazado, match="no tiene acceso"):
        cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil=None))


def test_dominio_ajeno_se_rechaza_sin_llamar_a_supabase():
    vistos: list = []
    with pytest.raises(LoginRechazado, match="dominio"):
        cuentas.iniciar_sesion("alguien@gmail.com", "clave-larga-1", cliente=_cliente(perfil="super_admin", vistos=vistos))
    assert vistos == []


def test_clave_incorrecta_da_un_mensaje_generico():
    with pytest.raises(LoginRechazado, match="Correo o clave incorrectos"):
        cuentas.iniciar_sesion("ana@santarosa.com.py", "mal", cliente=_cliente(login=400, perfil="super_admin"))


def test_supabase_caido_falla_cerrado_y_lo_dice():
    with pytest.raises(LoginRechazado, match="Intentá de nuevo") as e:
        cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(caido=True))
    assert e.value.transitorio


def test_el_padron_se_lee_con_el_jwt_del_usuario_y_no_con_otra_clave():
    vistos: list = []
    cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil="super_admin", vistos=vistos))
    lecturas = [r for r in vistos if "/rest/v1/" in r.url.path]
    assert lecturas and all(r.headers["authorization"] == "Bearer jwt-del-usuario" for r in lecturas)
    cuerpo = json.loads(next(r for r in vistos if r.url.path.endswith("/token")).content)
    assert cuerpo == {"email": "ana@santarosa.com.py", "password": "clave-larga-1"}


def test_bloqueo_tras_cinco_fallos_seguidos_aun_con_la_clave_correcta():
    malo = _cliente(login=400)
    for _ in range(5):
        with pytest.raises(LoginRechazado):
            cuentas.iniciar_sesion("ana@santarosa.com.py", "mal", cliente=malo, origen="1.2.3.4")
    with pytest.raises(LoginRechazado, match="Demasiados intentos"):
        cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil="super_admin"), origen="1.2.3.4")


def test_habilitado_exige_url_y_clave_anonima(monkeypatch):
    assert cuentas.habilitado()
    monkeypatch.setattr(cuentas.settings, "supabase_anon_key", "")
    assert not cuentas.habilitado()


def test_mapa_de_roles_invalido_no_abre_nada(monkeypatch):
    monkeypatch.setattr(cuentas.settings, "login_roles", "super_admin:dios,manager")
    with pytest.raises(LoginRechazado):
        cuentas.iniciar_sesion("ana@santarosa.com.py", "clave-larga-1", cliente=_cliente(perfil="super_admin"))
