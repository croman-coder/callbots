"""Diccionario de pronunciación en el panel: se edita en Voces, se manda al voice-agent y se usa al escuchar."""
from __future__ import annotations

import pytest

from app import pronunciaciones
from app.models import Pronunciation
from app.services import voicebox, voicestudio
from tests.test_ficha_agente import H, base, cliente  # noqa: F401  (fixtures)

D = [
    {"palabra": "Renault", "dicho": "Renó"},
    {"palabra": "GWM", "dicho": "Ge Doble Ve Eme"},
    {"palabra": "Santa Rosa", "dicho": "Santa Rosa Automotores"},
    {"palabra": "Santa", "dicho": "Santa María"},
]

# Los mismos casos están en services/voice-agent/tests/test_pronunciacion.py: las dos copias tienen que coincidir.
CASOS = [
    ("Le llamo por su Renault.", "Le llamo por su Renó."),
    ("RENAULT y renault", "Renó y Renó"),
    ("Una GWM nueva", "Una Ge Doble Ve Eme nueva"),
    ("Renaulta no es una marca", "Renaulta no es una marca"),
    ("Santa Rosa le saluda", "Santa Rosa Automotores le saluda"),
    ("Santa Lucía", "Santa María Lucía"),
    ("¿Es una Renault, una GWM?", "¿Es una Renó, una Ge Doble Ve Eme?"),
    ("", ""),
]


@pytest.mark.parametrize("texto,esperado", CASOS)
def test_aplicar(texto, esperado):
    assert pronunciaciones.aplicar(texto, D) == esperado


@pytest.mark.parametrize("palabra,dicho,motivo", [
    ("", "Renó", "tal como aparece"),
    ("Renault", "", "cómo se dice"),
    ("Renault", "Renault", "se dice igual"),
])
def test_validar_rechaza_lo_que_no_sirve(palabra, dicho, motivo):
    with pytest.raises(pronunciaciones.PronunciacionError, match=motivo):
        pronunciaciones.validar(palabra, dicho)


def test_validar_limpia_espacios_y_controles():
    assert pronunciaciones.validar("  Santa \n Rosa ", " Santa   Rosa Automotores\t") == ("Santa Rosa", "Santa Rosa Automotores")


# ─────────────────────────────────────────────── panel
def _filas(base):
    return {p.palabra: p.dicho for p in base().query(Pronunciation).all()}


def test_la_pantalla_lista_las_palabras_y_deja_agregar(cliente, base, monkeypatch):
    s = base()
    s.add(Pronunciation(palabra="Renault", dicho="Renó"))
    s.commit()
    monkeypatch.setattr(voicebox, "is_configured", lambda: False)
    r = cliente.get("/voices")
    assert r.status_code == 200 and 'id="pronunciacion"' in r.text
    assert "Renault" in r.text and 'value="Renó"' in r.text and 'action="/pronunciaciones"' in r.text


def test_agregar_y_cambiar_una_palabra(cliente, base):
    r = cliente.post("/pronunciaciones", data={"palabra": "Zeekr", "dicho": "Zíker"}, headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"] and r.headers["location"].endswith("#pronunciacion")
    assert _filas(base) == {"Zeekr": "Zíker"}
    # la misma palabra, escrita distinto, cambia la entrada en vez de duplicarla
    cliente.post("/pronunciaciones", data={"palabra": "ZEEKR", "dicho": "Zíkar"}, headers=H)
    assert _filas(base) == {"Zeekr": "Zíkar"}


def test_un_error_vuelve_con_el_motivo(cliente, base):
    r = cliente.post("/pronunciaciones", data={"palabra": "Renault", "dicho": "Renault"}, headers=H)
    assert r.status_code == 303 and "error=" in r.headers["location"] and _filas(base) == {}


def test_tiene_tope(cliente, base, monkeypatch):
    monkeypatch.setattr(pronunciaciones, "MAX_ENTRADAS", 2)
    for i in range(3):
        cliente.post("/pronunciaciones", data={"palabra": f"Marca{i}", "dicho": f"Marka {i}"}, headers=H)
    assert len(_filas(base)) == 2


def test_borrar(cliente, base):
    s = base()
    fila = Pronunciation(palabra="Renault", dicho="Renó")
    s.add(fila)
    s.commit()
    r = cliente.post(f"/pronunciaciones/{fila.id}/borrar", headers=H)
    assert r.status_code == 303 and "ok=" in r.headers["location"] and _filas(base) == {}
    assert "error=" in cliente.post("/pronunciaciones/999/borrar", headers=H).headers["location"]


def test_escuchar_aplica_el_diccionario_salvo_con_literal(cliente, base, monkeypatch):
    s = base()
    s.add(Pronunciation(palabra="Renault", dicho="Renó"))
    s.commit()
    dichos = []
    monkeypatch.setattr(voicestudio, "muestra", lambda perfil, texto: dichos.append(texto) or b"RIFF")
    monkeypatch.setattr(voicestudio.settings, "voicestudio_url", "http://voicestudio:3900")
    cliente.get("/voices/preview?profile_id=vs:abc&text=Su%20Renault%20nueva")
    cliente.get("/voices/preview?profile_id=vs:abc&text=Su%20Renault%20nueva&literal=1")
    assert dichos == ["Su Renó nueva", "Su Renault nueva"]


def test_el_voice_agent_recibe_el_diccionario(base, monkeypatch):
    s = base()
    s.add_all([Pronunciation(palabra="GWM", dicho="Ge Doble Ve Eme"), Pronunciation(palabra="Renault", dicho="Renó")])
    s.commit()
    assert [(e["palabra"], e["dicho"]) for e in pronunciaciones.lista(s)] == [("GWM", "Ge Doble Ve Eme"), ("Renault", "Renó")]


def test_el_endpoint_interno_manda_el_diccionario_junto_a_los_textos(base):
    from fastapi.testclient import TestClient

    from app import main
    from app.config import settings

    s = base()
    s.add(Pronunciation(palabra="Renault", dicho="Renó"))
    s.commit()
    r = TestClient(main.app).get("/internal/prompts", headers={"X-Callbot-Token": settings.internal_token})
    assert r.status_code == 200
    assert [(e["palabra"], e["dicho"]) for e in r.json()["pronunciaciones"]] == [("Renault", "Renó")]
