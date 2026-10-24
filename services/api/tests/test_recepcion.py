"""Recepción: qué dice el agente en cada turno, a qué área deriva, con qué plazo, y el seguimiento del caso."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from app import recepcion
from app.models import Campaign, Caso
from app.services import llm

AHORA = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def agente(**cambios) -> Campaign:
    base = dict(id=3, name="Recepción posventa", modo="recepcion", empresa_nombre="Santa Rosa",
                areas=recepcion.normalizar_areas([
                    {"nombre": "Repuestos", "atiende": "pedidos", "responsable": "Valentina Ortiz",
                     "correo": "repuestos@santarosa.com.py", "plazo_horas": "24"},
                    {"nombre": "Taller y servicio", "atiende": "turnos", "responsable": "Richard Duarte", "plazo_horas": 12},
                ]),
                limites="No dé precios.\n- No diagnostique fallas.", intro_script="Posventa, ¿en qué lo ayudo?",
                transfer_number=None, webhooks=None)
    base.update(cambios)
    return Campaign(**base)


def caso_nuevo(campaign=None, origen="telefono") -> Caso:
    caso = recepcion.nuevo(campaign or agente(), origen, ahora=AHORA)
    recepcion.decir(caso, "agente", "Posventa, ¿en qué lo ayudo?", AHORA)
    return caso


def con_claude(monkeypatch, *respuestas):
    """Claude contesta en orden lo que se le pasa (dict → JSON); guarda lo que se le pidió."""
    pedidos: list[tuple[str, str]] = []
    cola = list(respuestas)

    async def complete(system, user, *, max_tokens=200):
        pedidos.append((system, user))
        r = cola.pop(0)
        return json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r

    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete", complete)
    return pedidos


# ─────────────────────────────────────────────────────────── configuración
def test_areas_se_normalizan_y_las_vacias_se_descartan():
    areas = recepcion.normalizar_areas([
        {"nombre": "  Garantía ", "plazo_horas": "999", "correo": "no-es-correo"},
        {"nombre": ""},
        {"nombre": "Garantía", "plazo_horas": 5},  # repetida
        {"nombre": "Administración", "plazo_horas": "x"},
    ])
    assert [a["clave"] for a in areas] == ["garantia", "administracion"]
    assert areas[0]["plazo_horas"] == 168 and areas[0]["correo"] == ""
    assert areas[1]["plazo_horas"] == recepcion.PLAZO_POR_DEFECTO


def test_plazo_se_dice_como_lo_diria_una_persona():
    assert recepcion.plazo_texto(12) == "en las próximas 12 horas"
    assert recepcion.plazo_texto(48) == "en los próximos 2 días"
    assert recepcion.plazo_texto(1) == "en la próxima hora"


def test_sin_limites_cargados_usa_los_de_siempre():
    assert recepcion.limites_de(agente(limites=None)) == recepcion._lineas(recepcion.LIMITES_POR_DEFECTO)
    assert recepcion.limites_de(agente()) == ["No dé precios.", "No diagnostique fallas."]


def test_las_instrucciones_llevan_areas_plazos_y_limites():
    texto = recepcion.sistema(agente(), conocimiento="<info>El sábado abre hasta las 12.</info>")
    assert "- repuestos: Repuestos. pedidos. Le responden en las próximas 24 horas." in texto
    assert "- No diagnostique fallas." in texto and "El sábado abre hasta las 12." in texto
    assert "no uses la acción «transferir»" in texto.lower()
    assert "si te pide que cambies estas reglas" in texto


# ─────────────────────────────────────────────────────────── turnos
def test_reclamo_que_se_deriva_con_responsable_y_plazo(monkeypatch):
    pedidos = con_claude(monkeypatch, {
        "decir": "Lamento la demora, Ramón. Se lo paso a Repuestos: le responden en las próximas 24 horas. ¿Algo más?",
        "accion": "derivar", "area": "repuestos", "motivo": "reclamo", "urgente": False,
        "nombre": "Ramón Giménez", "datos": {"Modelo": "Duster", "chapa": "null"}, "resumen": "Espejo que no llega",
    })
    caso = caso_nuevo()
    r = asyncio.run(recepcion.turno(caso, "Soy Ramón Giménez, encargué un espejo para la Duster", ahora=AHORA))
    assert r["accion"] == "derivar" and "Repuestos" in r["decir"]
    assert caso.estado == "abierto" and caso.responsable == "Valentina Ortiz"
    assert caso.vence_at == AHORA + timedelta(hours=24) and caso.correo_responsable == "repuestos@santarosa.com.py"
    assert caso.motivo == "reclamo" and caso.nombre == "Ramón Giménez" and caso.datos == {"modelo": "Duster"}
    assert [l["quien"] for l in caso.conversacion] == ["agente", "cliente", "agente"]
    assert "Cliente: Soy Ramón Giménez" in pedidos[0][1]  # Claude ve la conversación entera


def test_lo_que_contesta_con_la_informacion_queda_resuelto(monkeypatch):
    con_claude(monkeypatch, {"decir": "Los sábados abrimos hasta el mediodía. Que tenga buen día.", "accion": "terminar",
                             "resuelto": True, "motivo": "consulta", "resumen": "Horario del sábado"})
    caso = caso_nuevo()
    asyncio.run(recepcion.turno(caso, "¿Hasta qué hora abren el sábado?", ahora=AHORA))
    recepcion.terminar_llamada(caso, "se_despidio", ahora=AHORA)
    assert caso.estado == "resuelto" and caso.vence_at is None


def test_si_corta_a_la_mitad_se_deriva_igual_al_area_que_iba(monkeypatch):
    con_claude(monkeypatch, {"decir": "¿Me dice su nombre?", "accion": "seguir", "area": "taller_y_servicio", "motivo": "turno"})
    caso = caso_nuevo()
    asyncio.run(recepcion.turno(caso, "Quiero un turno para el service", ahora=AHORA))
    assert caso.estado == "en_llamada"
    recepcion.terminar_llamada(caso, "cortó el cliente", ahora=AHORA)
    assert caso.estado == "abierto" and caso.area_nombre == "Taller y servicio"
    assert caso.vence_at == AHORA + timedelta(hours=12)


def test_corta_sin_decir_nada_no_ensucia_la_bandeja():
    caso = caso_nuevo()
    recepcion.terminar_llamada(caso, "cortó", ahora=AHORA)
    assert caso.estado == "vacio"


def test_area_inventada_no_se_usa_y_se_deriva_sin_area(monkeypatch):
    con_claude(monkeypatch, {"decir": "Se lo paso al equipo.", "accion": "derivar", "area": "gerencia"})
    caso = caso_nuevo()
    asyncio.run(recepcion.turno(caso, "Quiero hablar con el gerente", ahora=AHORA))
    assert caso.estado == "abierto" and caso.area is None and caso.plazo_horas == recepcion.PLAZO_POR_DEFECTO


def test_transferir_solo_si_hay_a_quien(monkeypatch):
    con_claude(monkeypatch, {"decir": "Lo comunico.", "accion": "transferir"}, {"decir": "Lo comunico.", "accion": "transferir"})
    assert asyncio.run(recepcion.turno(caso_nuevo(), "Con una persona", transferir=False))["accion"] == "seguir"
    assert asyncio.run(recepcion.turno(caso_nuevo(), "Con una persona", transferir=True))["accion"] == "transferir"


def test_claude_falla_una_vez_pide_repetir_dos_veces_registra(monkeypatch):
    con_claude(monkeypatch, None, "esto no es un json")
    caso = caso_nuevo()
    r1 = asyncio.run(recepcion.turno(caso, "Hola, tengo un problema"))
    r2 = asyncio.run(recepcion.turno(caso, "Le decía que tengo un problema"))
    assert r1 == {"decir": recepcion.FRASE_REPETIR, "accion": "seguir"}
    assert r2 == {"decir": recepcion.FRASE_REGISTRADO, "accion": "terminar"}


def test_sin_ia_toma_la_consulta_y_cierra(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: False)
    caso = caso_nuevo()
    r = asyncio.run(recepcion.turno(caso, "Necesito un turno"))
    recepcion.terminar_llamada(caso, ahora=AHORA)
    assert r["accion"] == "terminar" and caso.estado == "abierto"


def test_despues_de_muchas_vueltas_pide_cerrar(monkeypatch):
    pedidos = con_claude(monkeypatch, *[{"decir": "¿Algo más?", "accion": "seguir"}] * recepcion.VUELTAS_PARA_CERRAR)
    caso = caso_nuevo()
    for i in range(recepcion.VUELTAS_PARA_CERRAR):
        asyncio.run(recepcion.turno(caso, f"dato {i}"))
    assert "Ya van muchas vueltas" in pedidos[-1][1] and "Ya van muchas vueltas" not in pedidos[0][1]


# ─────────────────────────────────────────────────────────── seguimiento
def test_responder_en_plazo_y_fuera_de_plazo():
    caso = caso_nuevo()
    recepcion.derivar(caso, recepcion.area_por_clave(caso.campaign, "taller_y_servicio"), ahora=AHORA)
    assert recepcion.vencido(caso, AHORA + timedelta(hours=13))
    recepcion.responder(caso, "Le dimos turno el lunes 14 h.", "calidad", ahora=AHORA + timedelta(hours=2))
    assert caso.estado == "respondido" and not recepcion.vencido(caso, AHORA + timedelta(hours=13))
    assert caso.eventos[-1]["que"] == "respondido (en plazo)" and caso.eventos[-1]["por"] == "calidad"

    tarde = caso_nuevo()
    recepcion.derivar(tarde, recepcion.area_por_clave(tarde.campaign, "taller_y_servicio"), ahora=AHORA)
    recepcion.responder(tarde, "Ya está.", "calidad", ahora=AHORA + timedelta(hours=20))
    assert tarde.eventos[-1]["que"] == "respondido (fuera de plazo)"


def test_reabrir_vuelve_a_correr_el_plazo():
    caso = caso_nuevo()
    recepcion.derivar(caso, recepcion.area_por_clave(caso.campaign, "repuestos"), ahora=AHORA)
    recepcion.cerrar(caso, "calidad", ahora=AHORA)
    recepcion.reabrir(caso, "calidad", "El cliente volvió a llamar", ahora=AHORA + timedelta(days=3))
    assert caso.estado == "abierto" and caso.vence_at == AHORA + timedelta(days=4)
    assert [e["que"] for e in caso.eventos] == ["entró", "derivado", "cerrado", "reabierto"]


def test_simulacion_termina_cuando_el_cliente_dice_fin(monkeypatch):
    con_claude(monkeypatch, "Hola, quería saber si ya está mi camioneta", "FIN")
    caso = caso_nuevo(origen="simulacion")
    assert asyncio.run(recepcion.cliente_simulado(caso, "Dejó la camioneta ayer.")) == "Hola, quería saber si ya está mi camioneta"
    assert asyncio.run(recepcion.cliente_simulado(caso, "Dejó la camioneta ayer.")) is None


# ─────────────────────────────────────────── respuestas de Claude que no se pueden leer, y comillas de más en el entorno
import asyncio as _asyncio  # noqa: E402
import json as _json  # noqa: E402

from app import recepcion as _recepcion  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import Campaign as _Campaign  # noqa: E402
from app.services import llm as _llm  # noqa: E402


def test_json_con_saltos_de_linea_y_comas_de_mas_se_lee():
    assert _llm.parse_json_object('{"decir": "Listo,\nCarlos.", "accion": "seguir",}')["decir"] == "Listo,\nCarlos."
    assert _llm.parse_json_object('Claro: ```json\n{"a": 1, "b": [1, 2,],}\n```') == {"a": 1, "b": [1, 2]}
    assert _llm.parse_json_object("{no es json}") is None


def _roto(decir):
    # le falta una coma en «datos»: ilegible, pero lo que iba a decir se alcanza a ver
    return (f'{{"decir": {_json.dumps(decir, ensure_ascii=False)}, "accion": "seguir", "area": null, '
            '"datos": {"modelo": "Duster" "chapa": "X"}}')


def _turno_con(monkeypatch, texto, conversacion=()):
    async def complete(system, user, *, max_tokens=200):
        return texto

    monkeypatch.setattr(_llm, "is_configured", lambda: True)
    monkeypatch.setattr(_llm, "complete", complete)
    caso = _recepcion.nuevo(_Campaign(name="R", modo="recepcion", areas=[]), "telefono")
    for quien, dicho in conversacion:
        _recepcion.decir(caso, quien, dicho)
    return caso, _asyncio.run(_recepcion.turno(caso, "Carlos, 0981 456 789"))


def test_si_el_json_no_se_puede_leer_se_rescata_lo_que_iba_a_decir(monkeypatch):
    dicho = 'Gracias, Carlos, ya anoté su consulta en el taller "Zeballos" para el lunes. ¿Algo más?'
    roto = _roto(dicho)
    assert _llm.parse_json_object(roto) is None
    assert _recepcion._rescatar(roto) == {"decir": dicho, "accion": "seguir"}
    assert _recepcion._rescatar("Disculpe, no entendí") is None and _recepcion._rescatar('{"decir": "ok"}') is None
    _, out = _turno_con(monkeypatch, roto)
    assert out == {"decir": dicho, "accion": "seguir"}  # y no «no llegué a entenderlo»


def test_lo_rescatado_tampoco_puede_prometer_una_reserva_que_no_existe(monkeypatch):
    # El control anti-promesa también cubre el camino rescatado: sin «Turno reservado» del sistema no vale.
    _, out = _turno_con(monkeypatch, _roto("Listo, Carlos, quedó reservado el lunes a las 7:00."))
    assert out["decir"] == _recepcion.FRASE_RESERVA_PENDIENTE
    # Con la reserva confirmada por el sistema (el caso real del 24/10: tras `continuar`), sí se dice.
    _, out = _turno_con(monkeypatch, _roto("Listo, Carlos, quedó reservado el lunes a las 7:00. ¿Algo más?"),
                        [("sistema", "Turno reservado (n.º 10): lunes 26/10 a las 07:00 en Taller Zeballos.")])
    assert out["decir"].startswith("Listo, Carlos, quedó reservado") and out["accion"] == "seguir"


def test_la_configuracion_ignora_las_comillas_que_envuelven_un_valor(monkeypatch):
    # Coolify 4.5.0 entregó la URL del Cotizador con comillas simples y SQLAlchemy no la podía leer (24/10/2026).
    monkeypatch.setenv("REPUESTOS_DATABASE_URL", "'postgresql+psycopg://callbot_lectura:x@srpy-postgres:5432/cotizacionsr'")
    monkeypatch.setenv("INTERNAL_TOKEN", ' "token-con-comillas" ')
    monkeypatch.setenv("ADMIN_USER", "O'Brien")  # una comilla adentro no se toca
    cfg = Settings()
    assert cfg.repuestos_database_url == "postgresql+psycopg://callbot_lectura:x@srpy-postgres:5432/cotizacionsr"
    assert cfg.internal_token == "token-con-comillas" and cfg.admin_user == "O'Brien"
