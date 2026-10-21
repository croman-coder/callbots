"""Vocabulario del agente: se edita en Configuración y viaja en el guion junto con el diccionario de pronunciación."""
from __future__ import annotations

from app import vocabulario
from app.models import Campaign, Pronunciation
from tests.test_buzon_y_transferencia import INTERNO, Sesion, _agente, api  # noqa: F401
from tests.test_ficha_agente import H, base, cliente  # noqa: F401


def test_de_texto_acepta_lineas_y_comas_sin_repetidas():
    assert vocabulario.de_texto("Renault, Kardian\nrenault;  Haval  H6 \n\n") == ["Renault", "Kardian", "Haval H6"]
    assert vocabulario.de_texto(None) == [] and vocabulario.normalizar("  ") is None
    assert vocabulario.normalizar("Renault, Kardian") == "Renault\nKardian"
    assert len(vocabulario.de_texto("\n".join(f"p{i}" for i in range(500)))) == vocabulario.MAX_PALABRAS


def test_para_whisper_primero_el_agente_despues_el_diccionario_y_con_tope():
    d = [{"palabra": "GWM"}, {"palabra": "Renault"}]
    assert vocabulario.para_whisper("Kardian\nRenault", d) == ["Kardian", "Renault", "GWM"]
    largas = "\n".join("x" * 39 + str(i) for i in range(40))
    salida = vocabulario.para_whisper(largas, d)
    assert sum(len(p) + 2 for p in salida) <= vocabulario.MAX_CARACTERES_PROMPT


def test_se_guarda_desde_configuracion(cliente, base):
    r = cliente.post("/campaigns/1/guardar", data={"tab": "configuracion", "vocabulario": "Kardian, Haval H6\nkardian"}, headers=H)
    assert r.status_code == 303 and base().get(Campaign, 1).vocabulario == "Kardian\nHaval H6"
    pagina = cliente.get("/campaigns/1?tab=configuracion").text
    assert "Palabras que tiene que entender" in pagina and "Agregar las marcas de Santa Rosa" in pagina


def test_el_guion_lleva_el_vocabulario_con_el_diccionario(Sesion, api):
    _, sesion = _agente(Sesion, vocabulario="Kardian\nMariano Roque Alonso")
    s = Sesion()
    s.add(Pronunciation(palabra="Renault", dicho="Renó"))
    s.commit()
    g = api.get(f"/internal/sessions/{sesion}/script").json()
    assert g["vocabulario"] == ["Kardian", "Mariano Roque Alonso", "Renault"]
