"""Pruebas del acceso desde el CRM (app/sso.py).

Cada prueba corresponde a una forma concreta en que alguien sin permiso podría
entrar al panel. Si una falla, no es un detalle: es una puerta abierta.
"""
from __future__ import annotations

import pytest

from app import sso

SECRETO = "s" * 40
OTRO = "o" * 40
AHORA = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _limpiar_usados():
    sso._usados.clear()
    yield
    sso._usados.clear()


def ticket(**cambios) -> str:
    payload = {
        "sub": "u-123",
        "email": "ana@santarosa.com.py",
        "rol": "calidad",
        "iat": AHORA,
        "exp": AHORA + 60,
        "jti": "jti-1",
    }
    payload.update(cambios)
    quitar = [k for k, v in payload.items() if v is None]
    for k in quitar:
        del payload[k]
    return sso.firmar("t1", payload, SECRETO)


def canjear(t: str, ahora: float = AHORA + 1):
    return sso.canjear_ticket(t, secreto=SECRETO, ahora=ahora)


# ------------------------------------------------------------- lo que sí entra
def test_ticket_valido_entra_con_su_rol():
    p = canjear(ticket())
    assert p is not None
    assert (p.usuario, p.rol, p.via) == ("ana@santarosa.com.py", "calidad", "crm")


@pytest.mark.parametrize("rol", ["calidad", "admin", "owner"])
def test_roles_permitidos(rol):
    assert canjear(ticket(rol=rol, jti=f"j-{rol}")) is not None


# ---------------------------------------------------------- lo que no entra
def test_un_ticket_no_se_puede_usar_dos_veces():
    t = ticket()
    assert canjear(t) is not None
    assert canjear(t) is None, "un ticket que quedó en un log no puede reusarse"


def test_ticket_vencido():
    assert canjear(ticket(), ahora=AHORA + 61) is None


def test_ticket_con_vida_excesiva_aunque_la_firma_sea_valida():
    # Un CRM mal configurado no puede emitir tickets de una semana.
    largo = ticket(exp=AHORA + 7 * 86400)
    assert canjear(largo) is None


def test_ticket_emitido_en_el_futuro():
    assert canjear(ticket(iat=AHORA + 3600, exp=AHORA + 3660)) is None


def test_desfase_de_reloj_chico_se_tolera():
    assert canjear(ticket(iat=AHORA + 3, exp=AHORA + 63)) is not None


def test_firma_manipulada():
    t = ticket()
    cuerpo = t.split(".")[1]
    forjado = f"t1.{cuerpo}.{'A' * 43}"
    assert canjear(forjado) is None


def test_payload_alterado_con_la_firma_original():
    partes = ticket(rol="calidad").split(".")
    otro = ticket(rol="admin", jti="otro").split(".")
    # cuerpo de "admin" con la firma de "calidad"
    assert canjear(f"t1.{otro[1]}.{partes[2]}") is None


def test_firmado_con_otro_secreto():
    ajeno = sso.firmar(
        "t1",
        {"sub": "x", "rol": "admin", "iat": AHORA, "exp": AHORA + 60, "jti": "z"},
        OTRO,
    )
    assert canjear(ajeno) is None


@pytest.mark.parametrize("rol", ["agent", "viewer", "tasador", "", None, 7])
def test_rol_no_permitido(rol):
    assert canjear(ticket(rol=rol)) is None


@pytest.mark.parametrize("campo", ["sub", "jti", "rol", "iat", "exp"])
def test_faltan_campos_obligatorios(campo):
    assert canjear(ticket(**{campo: None})) is None


@pytest.mark.parametrize("basura", ["", "abc", "t1.x", "t1.x.y.z", "t9.x.y", None, 12])
def test_basura(basura):
    assert canjear(basura) is None


def test_fechas_booleanas_no_valen_como_numeros():
    # True == 1 en Python: sin el chequeo, exp=True pasaría por "vencido hace décadas"
    # pero iat=True/exp=True darían vida 0 y podrían colarse por otros caminos.
    assert canjear(ticket(iat=True, exp=True)) is None


def test_payload_que_no_es_objeto():
    t = sso.firmar("t1", ["no", "es", "dict"], SECRETO)  # type: ignore[arg-type]
    assert canjear(t) is None


# ---------------------------------------------------- ticket y sesión separados
def test_un_ticket_no_vale_como_sesion():
    assert sso.leer_sesion(ticket(), secreto=SECRETO, ahora=AHORA + 1) is None


def test_una_sesion_no_vale_como_ticket():
    p = sso.Principal("ana@santarosa.com.py", "admin", "crm")
    s = sso.emitir_sesion(p, secreto=SECRETO, ahora=AHORA)
    assert canjear(s) is None


def test_las_llaves_de_ticket_y_sesion_son_distintas():
    assert sso._llave(SECRETO, "t1") != sso._llave(SECRETO, "s1")


# ------------------------------------------------------------------ sesión
def test_sesion_ida_y_vuelta():
    p = sso.Principal("ana@santarosa.com.py", "calidad", "crm")
    s = sso.emitir_sesion(p, secreto=SECRETO, ahora=AHORA)
    leida = sso.leer_sesion(s, secreto=SECRETO, ahora=AHORA + 10)
    assert leida == p


def test_sesion_vencida():
    p = sso.Principal("ana@santarosa.com.py", "calidad", "crm")
    s = sso.emitir_sesion(p, secreto=SECRETO, ahora=AHORA)
    assert sso.leer_sesion(s, secreto=SECRETO, ahora=AHORA + sso.TTL_SESION + 1) is None


def test_sesion_con_rol_que_ya_no_esta_permitido():
    # Si mañana se saca un rol de la lista, sus sesiones viejas dejan de valer.
    s = sso.firmar(
        "s1", {"u": "x", "rol": "agent", "iat": AHORA, "exp": AHORA + 100}, SECRETO
    )
    assert sso.leer_sesion(s, secreto=SECRETO, ahora=AHORA + 1) is None


def test_sesion_rotando_el_secreto_queda_invalidada():
    p = sso.Principal("ana@santarosa.com.py", "calidad", "crm")
    s = sso.emitir_sesion(p, secreto=SECRETO, ahora=AHORA)
    assert sso.leer_sesion(s, secreto=OTRO, ahora=AHORA + 1) is None


@pytest.mark.parametrize("basura", [None, "", "s1.x.y", "cualquier cosa"])
def test_sesion_basura(basura):
    assert sso.leer_sesion(basura, secreto=SECRETO, ahora=AHORA) is None


# ------------------------------------------------------------- falla cerrado
@pytest.mark.parametrize("corto", ["", "corto", "x" * 31])
def test_secreto_corto_deshabilita_todo(corto):
    assert not sso.habilitado(corto)
    assert sso.abrir("t1", ticket(), secreto=corto) is None
    with pytest.raises(RuntimeError):
        sso.firmar("t1", {"a": 1}, secreto=corto)


def test_secreto_de_32_alcanza():
    assert sso.habilitado("x" * 32)


# ---------------------------------------------------- contrato con el CRM
# Este ticket lo firma el CRM en JavaScript (api/_lib/callbot-sso.js, repo del CRM)
# y el CRM prueba contra la MISMA constante en tests/callbot-sso.test.js. Si esta
# prueba falla, Callbot y el CRM dejaron de entenderse: nadie puede abrir la pestaña
# Calidad y el síntoma en producción es un 403 mudo, sin ningún error visible.
SECRETO_CONTRATO = "contrato-callbot-crm-prueba-0123456789"
TICKET_DEL_CRM = (
    "t1.eyJlbWFpbCI6ImFuYUBzYW50YXJvc2EuY29tLnB5IiwiZXhwIjoxODAwMDAwMDYwLCJpYXQiOjE4MDAwMDAwMDAs"
    "Imp0aSI6IjAwMDAwMDAwLTAwMDAtNDAwMC04MDAwLTAwMDAwMDAwMDAwMSIsInJvbCI6ImNhbGlkYWQiLCJzdWIiOiIx"
    "MTExMTExMS0yMjIyLTMzMzMtNDQ0NC01NTU1NTU1NTU1NTUifQ.BbhJSVct7I-x-yHR3qSNEimP1Ipqz5-d3jI_U78QbVc"
)


def test_contrato_callbot_acepta_el_ticket_que_firma_el_crm():
    p = sso.canjear_ticket(TICKET_DEL_CRM, secreto=SECRETO_CONTRATO, ahora=1_800_000_001)
    assert p == sso.Principal("ana@santarosa.com.py", "calidad", "crm")


def test_contrato_el_ticket_del_crm_se_reproduce_byte_a_byte_desde_python():
    payload = sso.abrir("t1", TICKET_DEL_CRM, SECRETO_CONTRATO)
    assert payload == {
        "email": "ana@santarosa.com.py", "exp": 1_800_000_060, "iat": 1_800_000_000,
        "jti": "00000000-0000-4000-8000-000000000001", "rol": "calidad",
        "sub": "11111111-2222-3333-4444-555555555555",
    }
    # Firmar acá los mismos datos da el mismo ticket que firmó el CRM.
    assert sso.firmar("t1", payload, SECRETO_CONTRATO) == TICKET_DEL_CRM


def test_contrato_los_roles_de_callbot_son_los_que_el_crm_puede_firmar():
    # ROLES_CALLBOT (CRM) <-> ROLES_PERMITIDOS (Callbot). Si divergen, un rol
    # que el CRM deja pasar recibe un 403 acá, o al revés.
    assert sso.ROLES_PERMITIDOS == {"owner", "admin", "calidad"}
