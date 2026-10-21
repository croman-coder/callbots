"""Ingreso con la cuenta de los dashboards, de punta a punta sobre la app real."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import cuentas, main
from app.config import settings
from app.sso import Principal

import tests.test_acceso_http  # noqa: F401  (registra las rutas /_sonda)

NAVEGADOR = {"sec-fetch-dest": "document", "sec-fetch-mode": "navigate", "accept": "text/html,*/*"}


@pytest.fixture()
def cli(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://dashboards.test/supabase")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon")
    # base_url https: la cookie es Secure y el cliente de prueba no la manda por http.
    return TestClient(main.app, base_url="https://callbot.test", follow_redirects=False)


def _entra_como(monkeypatch, rol="admin"):
    def falso(email, clave, *, cliente=None, origen=""):
        if clave != "buena":
            raise cuentas.LoginRechazado("Correo o clave incorrectos.")
        return Principal(email.strip().lower(), rol, "dashboards"), "jwt-de-prueba", 3600

    monkeypatch.setattr(cuentas, "iniciar_sesion_con_token", falso)


def test_navegador_sin_sesion_va_al_formulario_con_el_destino(cli):
    r = cli.get("/_sonda?x=1", headers=NAVEGADOR)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2F_sonda%3Fx%3D1"


def test_curl_sin_sesion_sigue_recibiendo_401_basic(cli):
    r = cli.get("/_sonda")
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Basic"


def test_iframe_sin_sesion_no_se_redirige_al_login(cli):
    # Dentro del CRM el aviso dice que se vuelva a abrir la pestaña: un formulario de clave ahí confundiría.
    r = cli.get("/_sonda", headers={**NAVEGADOR, "sec-fetch-dest": "iframe"})
    assert r.status_code == 401 and "www-authenticate" not in r.headers


def test_el_formulario_se_muestra_y_no_se_cachea(cli):
    r = cli.get("/login?next=/reportes")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert 'name="next" value="/reportes"' in r.text and 'type="password"' in r.text
    assert 'autocomplete="current-password"' in r.text


def test_ingreso_correcto_abre_sesion_y_lleva_al_destino(cli, monkeypatch):
    _entra_como(monkeypatch, "admin")
    r = cli.post("/login", data={"correo": "Ana@santarosa.com.py", "clave": "buena", "next": "/_sonda"})
    assert r.status_code == 303 and r.headers["location"] == "/_sonda"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert cli.get("/_sonda").json() == {"usuario": "ana@santarosa.com.py", "rol": "admin", "via": "dashboards"}


def test_manager_entra_como_calidad_y_no_ve_diagnostico(cli, monkeypatch):
    _entra_como(monkeypatch, "calidad")
    cli.post("/login", data={"correo": "gerente@santarosa.com.py", "clave": "buena"})
    assert cli.get("/_sonda").status_code == 200
    assert cli.get("/health-detail/_sonda").status_code == 403


def test_clave_mala_muestra_el_motivo_y_no_abre_sesion(cli, monkeypatch):
    _entra_como(monkeypatch)
    r = cli.post("/login", data={"correo": "ana@santarosa.com.py", "clave": "mala", "next": "/"})
    assert r.status_code == 401 and "Correo o clave incorrectos." in r.text
    assert "set-cookie" not in r.headers
    assert 'value="ana@santarosa.com.py"' in r.text  # no se pierde lo escrito


def test_next_ajeno_no_es_un_redireccion_abierta(cli, monkeypatch):
    _entra_como(monkeypatch)
    for malo in ("https://malo.example/x", "//malo.example", "/\\malo.example"):
        r = cli.post("/login", data={"correo": "ana@santarosa.com.py", "clave": "buena", "next": malo})
        assert r.headers["location"] == "/"


def test_un_formulario_de_otro_sitio_no_puede_probar_claves(cli, monkeypatch):
    _entra_como(monkeypatch)
    r = cli.post("/login", data={"correo": "a@santarosa.com.py", "clave": "buena"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_supabase_caido_responde_503_y_lo_dice(cli, monkeypatch):
    def caido(*a, **k):
        raise cuentas.LoginRechazado("No se pudo verificar tu cuenta. Intentá de nuevo en un momento.", transitorio=True)

    monkeypatch.setattr(cuentas, "iniciar_sesion_con_token", caido)
    r = cli.post("/login", data={"correo": "a@santarosa.com.py", "clave": "x"})
    assert r.status_code == 503 and "Intentá de nuevo" in r.text


def test_salir_borra_la_cookie(cli, monkeypatch):
    _entra_como(monkeypatch)
    cli.post("/login", data={"correo": "ana@santarosa.com.py", "clave": "buena"})
    r = cli.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert 'callbot_session=""' in r.headers["set-cookie"] or "max-age=0" in r.headers["set-cookie"].lower()
    cli.cookies.clear()
    assert cli.get("/_sonda").status_code == 401


def test_sin_supabase_configurado_el_login_no_existe(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    c = TestClient(main.app, base_url="https://callbot.test", follow_redirects=False)
    assert c.get("/login").status_code == 404
    assert c.get("/_sonda", headers=NAVEGADOR).status_code == 401  # Basic de siempre


def test_una_llamada_de_script_no_se_redirige_al_formulario(cli):
    # fetch/XHR con la página ya abierta: un 303 al HTML del login rompería quien espera JSON.
    r = cli.get("/_sonda", headers={"sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "accept": "text/html,*/*"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Basic"


def test_un_post_sin_sesion_no_se_redirige(cli):
    r = cli.post("/sync-now", headers=NAVEGADOR)
    assert r.status_code == 401
