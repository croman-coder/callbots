"""Acceso al panel de punta a punta, sobre la aplicación real (sin base de datos).

Se agregan rutas de prueba que dependen de `require_admin` para ejercitar la
autenticación sin tocar la base. La restricción de calidad se prueba con una
ruta bajo /health-detail porque la regla es por prefijo de ruta.
"""
from __future__ import annotations

import base64
import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app import main, seguridad, sso
from app.config import settings
from app.deps import require_admin

SECRETO = settings.sso_secret


@main.app.get("/_sonda")
def _sonda(p=Depends(require_admin)):
    return {"usuario": p.usuario, "rol": p.rol, "via": p.via}


@main.app.get("/health-detail/_sonda")
def _sonda_diag(p=Depends(require_admin)):
    return {"ok": True}


@pytest.fixture()
def cliente():
    sso._usados.clear()
    return TestClient(main.app, follow_redirects=False)


def ticket(rol="calidad", jti=None, email="ana@santarosa.com.py", **cambios):
    ahora = time.time()
    payload = {"sub": "u-1", "email": email, "rol": rol, "iat": ahora, "exp": ahora + 60,
               "jti": jti or f"j-{time.time_ns()}"}
    payload.update(cambios)
    return sso.firmar("t1", payload, SECRETO)


def cookie_sesion(rol="calidad") -> dict:
    return {"Cookie": f"{sso.COOKIE}={sso.emitir_sesion(sso.Principal('ana@santarosa.com.py', rol, 'crm'), SECRETO)}"}


def basic(usuario, clave) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{usuario}:{clave}".encode()).decode()}


# --------------------------------------------------------------------- /sso
def test_ticket_valido_abre_sesion_con_cookie_segura(cliente):
    r = cliente.get("/sso", params={"ticket": ticket()})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    c = r.headers["set-cookie"].lower()
    assert f"{sso.COOKIE}=" in c
    assert "httponly" in c and "secure" in c and "samesite=lax" in c
    assert r.headers["cache-control"] == "no-store"


def test_la_cookie_emitida_abre_el_panel(cliente):
    r = cliente.get("/sso", params={"ticket": ticket(rol="calidad")})
    valor = r.headers["set-cookie"].split(";")[0]
    s = cliente.get("/_sonda", headers={"Cookie": valor})
    assert s.status_code == 200 and s.json()["rol"] == "calidad"


def test_ticket_reusado_no_abre_sesion(cliente):
    t = ticket(jti="unico")
    assert cliente.get("/sso", params={"ticket": t}).status_code == 303
    r = cliente.get("/sso", params={"ticket": t})
    assert r.status_code == 403 and "set-cookie" not in r.headers


@pytest.mark.parametrize("rol", ["agent", "viewer", "tasador"])
def test_rol_sin_acceso_no_entra(cliente, rol):
    r = cliente.get("/sso", params={"ticket": ticket(rol=rol)})
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_ticket_ajeno_o_basura_no_entra(cliente):
    ajeno = sso.firmar("t1", {"sub": "x", "rol": "admin", "iat": time.time(),
                              "exp": time.time() + 60, "jti": "z"}, "o" * 40)
    for t in (ajeno, "", "basura", "t1.a.b"):
        r = cliente.get("/sso", params={"ticket": t})
        assert r.status_code == 403 and "set-cookie" not in r.headers


def test_sso_no_existe_si_no_hay_secreto(cliente, monkeypatch):
    monkeypatch.setattr(settings, "sso_secret", "")
    assert cliente.get("/sso", params={"ticket": ticket()}).status_code == 404


@pytest.mark.parametrize("destino,esperado", [
    ("/reportes", "/reportes"),
    ("/?theme=light", "/?theme=light"),
    ("/targets?status=scheduled", "/targets?status=scheduled"),
    ("https://malo.example/", "/"),          # redirect abierto
    ("//malo.example/", "/"),                # esquema relativo
    ("/\\malo.example", "/"),                # barra invertida que algunos navegadores normalizan
    ("javascript:alert(1)", "/"),
    ("/sso?ticket=x", "/"),                  # no reentrar al canje
    ("", "/"),
])
def test_next_solo_acepta_rutas_propias(cliente, destino, esperado):
    r = cliente.get("/sso", params={"ticket": ticket(), "next": destino})
    assert r.status_code == 303 and r.headers["location"] == esperado


# ------------------------------------------------------------ require_admin
def test_sin_credenciales_pide_basic(cliente):
    r = cliente.get("/_sonda")
    assert r.status_code == 401 and r.headers["www-authenticate"].lower().startswith("basic")


def test_dentro_de_un_iframe_no_hay_desafio_sino_una_pagina(cliente):
    r = cliente.get("/_sonda", headers={"Sec-Fetch-Dest": "iframe"})
    assert r.status_code == 401
    assert "www-authenticate" not in r.headers, "el cuadro de Basic se bloquea en un iframe"
    assert "text/html" in r.headers["content-type"] and "Calidad" in r.text


def test_basic_de_operacion_entra_como_admin(cliente):
    r = cliente.get("/_sonda", headers=basic("operador", "clave-de-prueba-larga"))
    assert r.status_code == 200 and r.json() == {"usuario": "operador", "rol": "admin", "via": "basic"}


@pytest.mark.parametrize("usuario,clave", [
    ("operador", "mala"), ("otro", "clave-de-prueba-larga"), ("", ""),
    ("operador", "clavé-con-ñ-no-ascii"),    # no debe reventar con 500
    ("ñandú", "ñ"),
])
def test_basic_incorrecto_es_401_y_nunca_500(cliente, usuario, clave):
    assert cliente.get("/_sonda", headers=basic(usuario, clave)).status_code == 401


def test_basic_malformado_es_401(cliente):
    for cab in ("Basic", "Basic !!!no-es-base64!!!", "Bearer abc", "Basic " + base64.b64encode(b"sin-dos-puntos").decode()):
        assert cliente.get("/_sonda", headers={"Authorization": cab}).status_code == 401


def test_cookie_falsificada_no_entra(cliente):
    falsa = sso.firmar("s1", {"u": "x", "rol": "admin", "iat": time.time(), "exp": time.time() + 999}, "o" * 40)
    assert cliente.get("/_sonda", headers={"Cookie": f"{sso.COOKIE}={falsa}"}).status_code == 401


def test_calidad_no_llega_a_diagnostico(cliente):
    assert cliente.get("/health-detail/_sonda", headers=cookie_sesion("calidad")).status_code == 403


@pytest.mark.parametrize("rol", ["admin", "owner"])
def test_admin_y_owner_si_llegan_a_diagnostico(cliente, rol):
    assert cliente.get("/health-detail/_sonda", headers=cookie_sesion(rol)).status_code == 200


def test_operacion_por_basic_llega_a_diagnostico(cliente):
    assert cliente.get("/health-detail/_sonda", headers=basic("operador", "clave-de-prueba-larga")).status_code == 200


def test_calidad_si_llega_al_resto(cliente):
    assert cliente.get("/_sonda", headers=cookie_sesion("calidad")).status_code == 200


# ---------------------------------------------------------------- cabeceras
def test_solo_el_crm_puede_enmarcar(cliente):
    csp = cliente.get("/health").headers["content-security-policy"]
    assert csp == "frame-ancestors 'self' https://crm.santarosa.lat"


def test_las_cabeceras_van_tambien_en_las_respuestas_de_error(cliente):
    r = cliente.get("/_sonda")  # 401
    assert "frame-ancestors" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_frame_ancestors_descarta_lo_que_no_es_un_origen(monkeypatch):
    monkeypatch.setattr(settings, "frame_ancestors",
        "https://crm.santarosa.lat https://pegado.example; script-src * https://otro.example"
        "\r\nX-Inyectado: 1 *.malo.com http://localhost:3000")
    v = seguridad.frame_ancestors()
    assert v == "'self' https://crm.santarosa.lat https://otro.example http://localhost:3000"
    assert ";" not in v and "\n" not in v and "*" not in v
    # el que traía ';' pegado se descarta, pero se avisa al arrancar
    assert "https://pegado.example;" in seguridad.origenes_descartados()


def test_una_configuracion_correcta_no_descarta_nada():
    assert seguridad.origenes_descartados() == []


def test_frame_ancestors_vacio_solo_permite_el_propio_sitio(monkeypatch):
    monkeypatch.setattr(settings, "frame_ancestors", "")
    assert seguridad.frame_ancestors() == "'self'"


def test_las_fuentes_cargan_sin_autenticacion_y_con_cache(cliente):
    r = cliente.get("/static/fonts/inter-latin-400-normal.woff2")
    assert r.status_code == 200 and len(r.content) > 10_000
    assert "max-age" in r.headers["cache-control"]


def test_no_se_puede_salir_del_directorio_static(cliente):
    for ruta in ("/static/../main.py", "/static/%2e%2e/main.py", "/static/..%2fconfig.py"):
        assert cliente.get(ruta).status_code in (400, 404)
