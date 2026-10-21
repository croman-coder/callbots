"""Pantalla Usuarios: las cuentas son las de los dashboards. Sin red: la API de administración es un transporte simulado."""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import cuentas, main, usuarios
from app.config import settings
from app.sso import Principal

URL_DASH = "https://dashboards.test"
YO = "ana@santarosa.com.py"
ID_YO = "11111111-1111-1111-1111-111111111111"
ID_OTRO = "22222222-2222-2222-2222-222222222222"
ID_BAJA = "33333333-3333-3333-3333-333333333333"


def _gente():
    return [
        {"id": ID_YO, "email": YO, "display_name": "Ana", "role": "super_admin", "color": "#1E2A78", "brands": [], "baja": False,
         "last_sign_in_at": "2026-10-21T10:00:00Z", "created_at": "2026-01-01T00:00:00Z"},
        {"id": ID_OTRO, "email": "beto@santarosa.com.py", "display_name": "Beto", "role": "manager", "color": "#FFD54A", "brands": ["GWM"], "baja": False,
         "last_sign_in_at": None, "created_at": "2026-02-01T00:00:00Z"},
        {"id": ID_BAJA, "email": "carla@santarosa.com.py", "display_name": "Carla", "role": "brand", "color": "malo", "brands": ["JAC"], "baja": True,
         "last_sign_in_at": "2026-03-01T00:00:00Z", "created_at": "2026-02-01T00:00:00Z"},
    ]


def _api(*, status=200, respuesta=None, vistos=None, caido=False):
    """La API /api/admin-user de los dashboards: `list` devuelve la gente, lo demás responde `respuesta`."""

    def manejar(req: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(req.content or b"{}")
        if vistos is not None:
            vistos.append((req.headers.get("authorization"), cuerpo))
        if caido:
            raise httpx.ConnectError("sin red")
        if status != 200:
            return httpx.Response(status, json=respuesta or {})
        if cuerpo.get("action") == "list":
            return httpx.Response(200, json={"users": _gente()})
        return httpx.Response(200, json=respuesta if respuesta is not None else {"ok": True})

    return httpx.Client(transport=httpx.MockTransport(manejar))


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "dashboards_url", URL_DASH)
    monkeypatch.setattr(settings, "supabase_url", URL_DASH + "/supabase")
    monkeypatch.setattr(settings, "supabase_anon_key", "anon")
    monkeypatch.setattr(settings, "login_dominios", "santarosa.com.py")
    monkeypatch.setattr(settings, "login_roles", "super_admin:admin,manager:calidad")


# ─────────────────────────────────────────────── listar
def test_listar_ordena_y_traduce_a_lo_que_se_muestra():
    gente = usuarios.listar("tok", cliente=_api())
    assert [p["nombre"] for p in gente] == ["Ana", "Beto", "Carla"]  # los de baja al final
    ana, beto, carla = gente
    assert (ana["rol_nombre"], ana["acceso_nombre"]) == ("Super admin", "Administrador")
    assert (beto["rol_nombre"], beto["acceso_nombre"]) == ("Manager", "Calidad")
    assert carla["baja"] and carla["acceso"] is None
    assert carla["color"] == "#1E2A78"  # un color inválido no llega al estilo de la página
    assert beto["ultimo_ingreso"] is None and ana["ultimo_ingreso"].year == 2026


def test_listar_manda_el_token_de_quien_administra():
    vistos = []
    usuarios.listar("el-token", cliente=_api(vistos=vistos))
    assert vistos == [("Bearer el-token", {"action": "list"})]


@pytest.mark.parametrize("estado,reauth", [(401, True), (403, False)])
def test_sesion_vencida_o_sin_permiso_se_distinguen(estado, reauth):
    with pytest.raises(usuarios.UsuariosError) as e:
        usuarios.listar("tok", cliente=_api(status=estado))
    assert e.value.reautenticar is reauth and e.value.estado == estado


def test_dashboards_caido_no_deja_pasar_la_excepcion_de_red():
    with pytest.raises(usuarios.UsuariosError) as e:
        usuarios.listar("tok", cliente=_api(caido=True))
    assert e.value.estado == 502


def test_el_error_de_los_dashboards_se_muestra_tal_cual():
    with pytest.raises(usuarios.UsuariosError, match="ya está registrado"):
        usuarios.ejecutar("tok", YO, {"accion": "crear", "correo": "x@santarosa.com.py", "rol": "manager"},
                          cliente=_api(status=400, respuesta={"error": "El correo ya está registrado."}))


# ─────────────────────────────────────────────── crear
def _crear(**datos):
    vistos = []
    base = {"accion": "crear", "correo": "Nuevo@SantaRosa.com.py", "nombre": "  Nuevo   Nombre ", "rol": "manager", "marcas": ["GWM", "NO_EXISTE", "GWM"]}
    aviso = usuarios.ejecutar("tok", YO, {**base, **datos}, cliente=_api(vistos=vistos, respuesta={"ok": True, "invitacion": {"enviado": True}}))
    return aviso, [c for _, c in vistos]


def test_crear_con_invitacion_normaliza_y_filtra_las_marcas():
    aviso, cuerpos = _crear()
    assert cuerpos == [{"action": "create", "email": "nuevo@santarosa.com.py", "display_name": "Nuevo Nombre", "role": "manager",
                        "brands": ["GWM"], "invitar": True}]
    assert "invitación" in aviso


def test_crear_con_clave_inicial_la_manda_y_avisa_que_hay_que_cambiarla():
    aviso, cuerpos = _crear(invitar=False, clave="una-clave-larga")
    assert cuerpos[0]["password"] == "una-clave-larga" and cuerpos[0]["invitar"] is False
    assert "cambiar la clave" in aviso


@pytest.mark.parametrize("datos,motivo", [
    ({"correo": "no-es-correo"}, "no parece válido"),
    ({"correo": "alguien@gmail.com"}, "tiene que ser de santarosa.com.py"),
    ({"rol": "dios"}, "Elegí un rol"),
    ({"invitar": False, "clave": "corta"}, "al menos 8"),
])
def test_crear_rechaza_lo_invalido_sin_llamar_a_los_dashboards(datos, motivo):
    vistos = []
    base = {"accion": "crear", "correo": "ok@santarosa.com.py", "rol": "manager"}
    with pytest.raises(usuarios.UsuariosError, match=motivo):
        usuarios.ejecutar("tok", YO, {**base, **datos}, cliente=_api(vistos=vistos))
    assert vistos == []


def test_si_la_invitacion_no_salio_lo_dice():
    aviso = usuarios.ejecutar("tok", YO, {"accion": "crear", "correo": "z@santarosa.com.py", "rol": "manager"},
                              cliente=_api(respuesta={"ok": True, "invitacion": {"enviado": False}}))
    assert "no se pudo mandar la invitación" in aviso


# ─────────────────────────────────────────────── editar, baja, reactivar, clave
def _ejecutar(datos, vistos=None):
    return usuarios.ejecutar("tok", YO, datos, cliente=_api(vistos=vistos))


def test_editar_cambia_nombre_rol_y_marcas():
    vistos = []
    aviso = _ejecutar({"accion": "editar", "id": ID_OTRO, "nombre": "Alberto", "rol": "brand", "marcas": ["JAC", "XXX"]}, vistos)
    acciones = [c["action"] for _, c in vistos if c["action"] != "list"]
    assert acciones == ["update", "set_brands"]
    assert vistos[-1][1]["brands"] == ["JAC"] and "Alberto" in aviso


def test_nadie_se_cambia_el_propio_rol_ni_se_da_de_baja_ni_se_resetea_la_clave():
    for datos, motivo in [
        ({"accion": "editar", "id": ID_YO, "rol": "manager"}, "propio rol"),
        ({"accion": "baja", "id": ID_YO}, "darte de baja"),
        ({"accion": "clave", "id": ID_YO, "clave": "clave-larga-1"}, "propia clave"),
    ]:
        vistos = []
        with pytest.raises(usuarios.UsuariosError, match=motivo):
            _ejecutar(datos, vistos)
        assert [c["action"] for _, c in vistos] == ["list"]  # solo consultó: no escribió nada


def test_editarse_a_uno_mismo_sin_cambiar_el_rol_si_se_puede():
    assert "Se guardaron" in _ejecutar({"accion": "editar", "id": ID_YO, "nombre": "Ana María", "rol": "super_admin"})


def test_baja_y_reactivar_usan_las_acciones_de_los_dashboards():
    vistos = []
    _ejecutar({"accion": "baja", "id": ID_OTRO}, vistos)
    _ejecutar({"accion": "reactivar", "id": ID_BAJA}, vistos)
    escrituras = [c for _, c in vistos if c["action"] != "list"]
    assert escrituras == [{"action": "deactivate", "user_id": ID_OTRO}, {"action": "reactivate", "user_id": ID_BAJA}]


def test_nueva_clave_exige_largo_y_no_viaja_al_aviso():
    with pytest.raises(usuarios.UsuariosError, match="al menos 8"):
        _ejecutar({"accion": "clave", "id": ID_OTRO, "clave": "corta"})
    vistos = []
    aviso = _ejecutar({"accion": "clave", "id": ID_OTRO, "clave": "una-clave-larga-1"}, vistos)
    assert vistos[-1][1] == {"action": "reset_password", "user_id": ID_OTRO, "new_password": "una-clave-larga-1"}
    assert "una-clave-larga-1" not in aviso


@pytest.mark.parametrize("datos,motivo", [
    ({"accion": "borrar", "id": ID_OTRO}, "no existe"),  # borrar cuentas no se hace desde Callbot
    ({"accion": "baja", "id": "no-es-un-uuid"}, "no existe"),
    ({"accion": "baja", "id": "99999999-9999-9999-9999-999999999999"}, "ya no está"),
])
def test_acciones_o_personas_que_no_existen(datos, motivo):
    with pytest.raises(usuarios.UsuariosError, match=motivo):
        _ejecutar(datos)


# ─────────────────────────────────────────────── ingreso: el token
def _gotrue(vistos=None):
    def manejar(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/auth/v1/token"):
            return httpx.Response(200, json={"access_token": "jwt-de-ana", "expires_in": 3600, "user": {"id": ID_YO}})
        if req.url.path.endswith("/rest/v1/profiles"):
            return httpx.Response(200, json=[{"role": "super_admin"}])
        return httpx.Response(200, json=[])

    return httpx.Client(transport=httpx.MockTransport(manejar))


def test_iniciar_sesion_con_token_devuelve_el_jwt_y_cuanto_dura():
    cuentas._reiniciar_intentos()
    principal, token, vence = cuentas.iniciar_sesion_con_token(YO, "clave-larga-1", cliente=_gotrue())
    assert (principal.usuario, principal.rol) == (YO, "admin") and token == "jwt-de-ana" and vence == 3600


# ─────────────────────────────────────────────── HTTP
@pytest.fixture()
def cli():
    return TestClient(main.app, base_url="https://callbot.test", follow_redirects=False)


def _entra(cli, monkeypatch, *, rol="admin", usuario=YO, vence=3600):
    monkeypatch.setattr(cuentas, "iniciar_sesion_con_token",
                        lambda email, clave, *, cliente=None, origen="": (Principal(usuario, rol, "dashboards"), "jwt-de-prueba", vence))
    return cli.post("/login", data={"correo": usuario, "clave": "x", "next": "/usuarios"})


def test_el_administrador_se_lleva_el_token_solo_para_usuarios(cli, monkeypatch):
    r = _entra(cli, monkeypatch)
    sb = [c for c in r.headers.get_list("set-cookie") if c.startswith("callbot_sb=")]
    assert sb and "path=/usuarios" in sb[0].lower() and "httponly" in sb[0].lower() and "samesite=strict" in sb[0].lower()


def test_calidad_no_recibe_el_token_ni_entra_a_usuarios(cli, monkeypatch):
    r = _entra(cli, monkeypatch, rol="calidad")
    assert not [c for c in r.headers.get_list("set-cookie") if c.startswith("callbot_sb=")]
    assert cli.get("/usuarios").status_code == 403
    assert cli.post("/usuarios/api", json={"accion": "baja"}).status_code == 403


def test_salir_borra_tambien_el_token(cli, monkeypatch):
    _entra(cli, monkeypatch)
    r = cli.post("/logout")
    assert any(c.startswith("callbot_sb=") and ("max-age=0" in c.lower() or '=""' in c) for c in r.headers.get_list("set-cookie"))


def test_sin_cuenta_de_dashboards_la_pantalla_lo_explica(cli, monkeypatch):
    _entra(cli, monkeypatch, usuario="operador")  # credencial de operación: no es un correo
    r = cli.get("/usuarios")
    assert r.status_code == 200 and "tu cuenta de los dashboards" in r.text


def test_sin_token_pide_confirmar_la_clave(cli, monkeypatch):
    _entra(cli, monkeypatch)
    cli.cookies.delete("callbot_sb", path="/usuarios")
    r = cli.get("/usuarios")
    assert r.status_code == 200 and "Confirmá tu clave" in r.text and 'type="password"' in r.text
    assert 'aria-label="Mostrar clave"' in r.text or "Mostrar clave" in r.text


def test_la_lista_se_muestra_con_el_token_de_la_cookie(cli, monkeypatch):
    _entra(cli, monkeypatch)
    vistos = []
    monkeypatch.setattr(usuarios, "_llamar", lambda token, cuerpo, *, cliente=None: (vistos.append(token) or {"users": _gente()}))
    r = cli.get("/usuarios")
    assert r.status_code == 200 and vistos == ["jwt-de-prueba"]
    assert "beto@santarosa.com.py" in r.text and "Nuevo usuario" in r.text and r.headers["cache-control"] == "no-store"
    # la propia fila no ofrece darse de baja ni cambiarse la clave
    ana = r.text.split('data-id="' + ID_YO + '"')[1].split("</tr>")[0]
    assert "Dar de baja" not in ana and "Nueva clave" not in ana


def test_sesion_de_dashboards_vencida_vuelve_a_pedir_la_clave_y_borra_la_cookie(cli, monkeypatch):
    _entra(cli, monkeypatch)

    def vencida(token, cuerpo, *, cliente=None):
        raise usuarios.UsuariosError("Venció tu sesión de administración. Confirmá tu clave para seguir.", estado=401, reautenticar=True)

    monkeypatch.setattr(usuarios, "_llamar", vencida)
    r = cli.get("/usuarios")
    assert r.status_code == 200 and "Confirmá tu clave" in r.text and "Venció tu sesión" in r.text
    assert any(c.startswith("callbot_sb=") for c in r.headers.get_list("set-cookie"))


def test_confirmar_la_clave_guarda_el_token(cli, monkeypatch):
    _entra(cli, monkeypatch)
    cli.cookies.delete("callbot_sb", path="/usuarios")
    r = cli.post("/usuarios/acceso", data={"clave": "buena"})
    assert r.status_code == 303 and r.headers["location"] == "/usuarios"
    assert any(c.startswith("callbot_sb=jwt-de-prueba") for c in r.headers.get_list("set-cookie"))


def test_confirmar_con_clave_mala_no_guarda_nada(cli, monkeypatch):
    _entra(cli, monkeypatch)

    def malo(*a, **k):
        raise cuentas.LoginRechazado("Correo o clave incorrectos.")

    monkeypatch.setattr(cuentas, "iniciar_sesion_con_token", malo)
    r = cli.post("/usuarios/acceso", data={"clave": "mala"})
    assert r.status_code == 401 and "incorrectos" in r.text
    assert not any(c.startswith("callbot_sb=") for c in r.headers.get_list("set-cookie"))


def test_api_sin_token_responde_401_para_reconfirmar(cli, monkeypatch):
    _entra(cli, monkeypatch)
    cli.cookies.delete("callbot_sb", path="/usuarios")
    r = cli.post("/usuarios/api", json={"accion": "baja", "id": ID_OTRO})
    assert r.status_code == 401 and r.json()["reautenticar"] is True


def test_api_ejecuta_y_devuelve_el_aviso(cli, monkeypatch):
    _entra(cli, monkeypatch)
    monkeypatch.setattr(usuarios, "ejecutar", lambda token, yo, datos, *, cliente=None: f"hecho {datos['accion']} por {yo} con {token}")
    r = cli.post("/usuarios/api", json={"accion": "baja", "id": ID_OTRO})
    assert r.status_code == 200 and r.json() == {"ok": f"hecho baja por {YO} con jwt-de-prueba"}


def test_api_devuelve_el_motivo_si_algo_no_se_puede(cli, monkeypatch):
    _entra(cli, monkeypatch)

    def no(*a, **k):
        raise usuarios.UsuariosError("No podés darte de baja a vos mismo.")

    monkeypatch.setattr(usuarios, "ejecutar", no)
    r = cli.post("/usuarios/api", json={"accion": "baja", "id": ID_YO})
    assert r.status_code == 400 and r.json()["error"] == "No podés darte de baja a vos mismo."
    assert cli.post("/usuarios/api", content=b"no es json", headers={"content-type": "application/json"}).status_code == 400
    assert cli.post("/usuarios/api", json=["lista"]).status_code == 400


def test_el_login_tiene_el_ojito_y_carga_el_movimiento(cli):
    r = cli.get("/login")
    assert r.status_code == 200
    assert 'class="ojo"' in r.text and "aria-pressed" in r.text and "/static/vendor/motion.min.js" in r.text
    assert 'name="clave" type="password"' in r.text
