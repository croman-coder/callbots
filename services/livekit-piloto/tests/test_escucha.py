"""Cuándo meter una muletilla (agente/escucha.py) y cómo se acondiciona un clip (agente/clips.py): sin LiveKit ni GPU."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente.clips import PICO, acondicionar  # noqa: E402
from agente.escucha import Espaciador, Racha  # noqa: E402


def test_espaciador_respeta_el_intervalo_y_el_maximo():
    e = Espaciador(cada=10, maximo=2)
    assert e.puede(0)
    e.marcar(0)
    assert not e.puede(5) and e.puede(10)
    e.marcar(10)
    assert not e.puede(100)  # ya fueron dos


def test_no_hay_muletilla_en_una_respuesta_corta():
    r = Racha(desde=3.0, cada=7.0)
    r.inicio_de_voz(10.0)  # «Le doy un nueve»: habla 1,2 s
    assert not r.fin_de_voz(11.2, agente_escuchando=True)


def test_hay_muletilla_en_una_pausa_de_una_respuesta_larga_y_no_seguido():
    r = Racha(desde=3.0, cada=7.0)
    r.inicio_de_voz(0.0)
    assert not r.fin_de_voz(2.0, True)  # primera pausa: todavía no lleva 3 s
    r.inicio_de_voz(2.3)  # sigue (pausa de 0,3 s: la misma racha)
    assert r.fin_de_voz(4.0, True)  # lleva 4 s: toca
    r.inicio_de_voz(4.3)
    assert not r.fin_de_voz(6.5, True)  # hace 2,5 s de la anterior: no
    r.inicio_de_voz(6.8)
    assert r.fin_de_voz(11.5, True)  # pasaron más de 7 s


def test_una_pausa_larga_empieza_una_frase_nueva():
    r = Racha(desde=3.0, cada=7.0, pausa_nueva=0.8)
    r.inicio_de_voz(0.0)
    r.fin_de_voz(1.0, True)
    r.inicio_de_voz(5.0)  # 4 s después: es otra frase, la racha arranca de cero
    assert not r.fin_de_voz(6.5, True)


def test_nunca_con_el_agente_hablando():
    r = Racha(desde=3.0, cada=7.0)
    r.inicio_de_voz(0.0)
    assert not r.fin_de_voz(5.0, agente_escuchando=False)
    r.inicio_de_voz(5.3)
    assert r.fin_de_voz(6.0, agente_escuchando=True)  # y no gastó el cupo en el intento anterior


def test_acondicionar_recorta_suaviza_y_normaliza():
    sr = 24000
    x = np.concatenate([np.zeros(sr // 2), 0.3 * np.sin(np.linspace(0, 800, sr // 2)), np.zeros(sr // 2)]).astype(np.float32)
    y = acondicionar(x, sr)
    assert len(y) < len(x) * 0.5  # sin el silencio de los lados
    assert abs(float(np.abs(y).max()) - PICO) < 1e-3
    assert y[0] == 0 and abs(y[-1]) < 1e-3  # sin clics


def test_acondicionar_un_clip_en_silencio_queda_vacio():
    assert len(acondicionar(np.zeros(1000, dtype=np.float32), 24000)) == 0
