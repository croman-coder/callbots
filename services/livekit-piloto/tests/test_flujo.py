"""El guion de la encuesta (agente/flujo.py) con las respuestas reales de las pruebas: sin LiveKit, sin LLM, sin GPU."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agente import flujo  # noqa: E402
from agente.flujo import Estado, Turnos, avanzar  # noqa: E402


def _conversar(*dichos: str) -> tuple[Estado, list]:
    e = Estado()
    return e, [avanzar(e, d) for d in dichos]


def test_camino_feliz_con_las_respuestas_de_la_prueba_en_la_dgx():
    e, d = _conversar("Sí, dale.", "Le doy un nueve.", "Le doy un diez.", "Bueno, ocho.", "Dies", "Nueve", "Nada, todo perfecto.")
    assert [x.anotado for x in d] == ["acepta", "nota:1=9", "nota:2=10", "nota:3=8", "nota:4=10", "nota:5=9", "fin"]
    assert e.notas == {1: 9, 2: 10, 3: 8, 4: 10, 5: 9}
    assert d[-1].cerrar and e.paso == "cierre" and e.comentario == "Nada, todo perfecto."
    # cada turno sale con su pregunta siguiente, con las palabras exactas
    assert d[0].fijo.endswith(flujo.PREGUNTAS[1]) and d[1].fijo.endswith(flujo.PREGUNTAS[2])
    assert d[4].fijo.endswith(flujo.PREGUNTAS[5]) and d[5].fijo.endswith(flujo.PREGUNTAS[6])


def test_las_notas_se_leen_con_las_reglas_de_callbot():
    # «Cero» y «dies» (seseo de Whisper) son notas, no ruido; «un diez» no es un uno.
    e, _ = _conversar("sí", "Cero.", "no me gustó nada", "Le doy un diez")
    assert e.notas[1] == 0 and e.motivos[1] == "no me gustó nada" and e.notas[2] == 10


def test_nota_baja_pregunta_que_paso_antes_de_seguir():
    e, d = _conversar("sí", "Cero.")
    assert d[1].fijo.endswith(flujo.PREGUNTA_MOTIVO) and e.paso == "motivo" and d[1].pista is None
    d2 = avanzar(e, "Tardaron mucho y nadie me avisó.")
    assert d2.fijo.endswith(flujo.PREGUNTAS[2]) and e.paso == "pregunta" and e.n == 2 and d2.pista == "numeros"
    assert 1 in e.motivos


def test_seis_es_baja_siete_no():
    assert _conversar("sí", "seis")[0].paso == "motivo"
    e, d = _conversar("sí", "siete")
    assert e.paso == "pregunta" and e.n == 2


def test_respuesta_que_no_se_entiende_se_repregunta_una_vez_y_despues_se_sigue():
    e, d = _conversar("sí", "mmm no sé, depende")
    assert d[1].fijo is None and d[1].anotado == "repregunta:1" and e.n == 1 and d[1].pista == "numeros"
    d2 = avanzar(e, "y qué sé yo")
    assert d2.anotado == "sin-nota:1" and e.notas[1] is None and e.n == 2 and d2.fijo.endswith(flujo.PREGUNTAS[2])


def test_pregunta_fuera_de_la_encuesta_la_atiende_el_llm_no_el_texto_fijo():
    e, d = _conversar("sí", "¿cuánto me va a costar la próxima revisión?")
    assert d[1].fijo is None and "Calidad" in d[1].directiva and e.n == 1


def test_no_tan_satisfecho_no_se_anota_como_nota():
    # regla de Callbot (3/11/2026): «no tan satisfecho» no es un 8
    e, d = _conversar("sí", "no tan satisfecho")
    assert 1 not in e.notas and d[1].anotado == "repregunta:1"


def test_cortes_baja_ocupado_y_rechazo():
    for dicho, corte in [("no me llamen más", "baja"), ("ahora no puedo, estoy manejando", "ocupado"), ("no", "rechazo")]:
        e, d = _conversar(dicho)
        assert e.corte == corte and d[0].cerrar and d[0].fijo and e.paso == "cierre", dicho
    # la baja vale en cualquier punto de la encuesta
    e, d = _conversar("sí", "no me llamen más")
    assert e.corte == "baja" and d[1].cerrar


def test_ocupado_no_corta_un_comentario_libre():
    # «no puedo» dentro del comentario de la pregunta 6 es una opinión, no un corte.
    e, d = _conversar("sí", "9", "9", "9", "9", "9", "la verdad que no puedo quejarme, todo bien")
    assert d[-1].anotado == "fin" and e.corte == "" and "quejarme" in e.comentario


def test_saludo_poco_claro_se_aclara_una_vez_y_despues_arranca():
    e, d = _conversar("¿quién habla?", "mmm")
    assert d[0].fijo is None and d[0].pista == "saludo" and d[0].anotado == "saludo-no-claro"
    assert d[1].anotado == "acepta" and e.paso == "pregunta" and e.n == 1


def test_acuses_no_se_repiten_en_la_llamada():
    e, d = _conversar("sí", "10", "10", "10", "10", "10")
    # cinco notas altas seguidas: cinco agradecimientos distintos
    assert len(e.acuses) == len(set(e.acuses)) == 5, [x.fijo for x in d[1:]]


def test_la_memoria_por_turno_no_avanza_dos_pasos_si_livekit_repite_la_llamada():
    t = Turnos()
    t.decidir(1, "sí")
    a = t.decidir(2, "Le doy un nueve.")
    n = t.estado.n
    assert t.decidir(2, "Le doy un nueve.") is a and t.estado.n == n == 2
    # la generación definitiva trae otro texto para el mismo turno: se rehace desde el estado anterior
    b = t.decidir(2, "Le doy un cero.")
    assert b.anotado == "nota:1=0" and t.estado.notas == {1: 0} and t.estado.paso == "motivo"


def test_directivas_no_nombran_marcas_ni_herramientas():
    e, d = _conversar("sí", "8", "2", "malo")
    for x in d:
        assert "guardar_respuesta" not in x.directiva and "herramienta" not in x.directiva.lower()


def test_lo_inaudible_se_repregunta_y_no_se_toma_por_un_no():
    from agente.texto import MARCA_INAUDIBLE

    e, d = _conversar(MARCA_INAUDIBLE)
    assert e.paso == "saludo" and d[0].anotado == "saludo-no-claro" and e.corte == ""
    e, d = _conversar("sí", MARCA_INAUDIBLE, MARCA_INAUDIBLE)
    assert d[1].anotado == "repregunta:1" and d[2].anotado == "sin-nota:1" and e.notas == {1: None} and e.corte == ""
    e, d = _conversar("sí", "0", MARCA_INAUDIBLE)
    assert e.motivos == {} and e.n == 2 and e.paso == "pregunta"
