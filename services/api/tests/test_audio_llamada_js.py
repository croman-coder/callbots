"""Audio de las pruebas desde el navegador: el filtro del micrófono y de la voz del agente (static/audio-llamada.js).

La prueba de verdad está en tests/js (Node): respuesta en frecuencia, ganancia, tramas y réplicas. Acá solo se corre y
se comprueba que las dos páginas que usan el audio lo cargan.
"""
import pathlib
import shutil
import subprocess

import pytest

AQUI = pathlib.Path(__file__).parent
APP = AQUI.parent / "app"


@pytest.mark.skipif(shutil.which("node") is None, reason="sin node no se puede probar el audio del navegador")
def test_el_remuestreador_del_navegador_filtra_y_no_deja_replicas():
    r = subprocess.run(["node", "--test", str(AQUI / "js" / "audio_llamada.test.mjs")], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout[-2000:] + r.stderr[-500:])


def test_la_prueba_del_agente_y_el_simulador_cargan_el_audio_con_filtro():
    for pagina in ("agente.html", "simulator.html"):
        html = (APP / "templates" / pagina).read_text(encoding="utf-8")
        assert "/static/audio-llamada.js?v={{ audio_llamada_v }}" in html, pagina
    agente = (APP / "static" / "agente.js").read_text(encoding="utf-8")
    assert "AudioLlamada.crearReproductor" in agente and "createBufferSource" not in agente  # una fuente por trama metía clics
    assert "suma / (b - a)" not in agente  # promediar no es un filtro
