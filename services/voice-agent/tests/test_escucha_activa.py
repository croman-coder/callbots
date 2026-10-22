"""Escucha activa en la encuesta: el agente muestra que escuchó cada respuesta, la pregunta siguiente no arranca con
un «Muy bien, gracias» fijo, cierra con empatía si le fue mal, y un ruido corto no se toma como respuesta."""
from __future__ import annotations

from app import dialog
from app.api_client import FRASE_CIERRE_DISCONFORME, Question
from app.listener import ListenResult, Recording
from tests.test_buzon_y_transferencia import Api, correr, entorno, guion, habla, silencio, verdict  # noqa: F401
from tests.test_recepcion import ApiRecepcion, recepcion


def preguntas():
    return [
        Question(id=1, position=1, text="Primero, ¿qué tan satisfecho quedó con el trabajo?", qtype="scale_1_10",
                 is_required=True, max_answer_seconds=10, max_retries=1,
                 texto_sin_acuse="Primero, ¿qué tan satisfecho quedó con el trabajo?"),
        Question(id=2, position=2, text="Muy bien, gracias. ¿Y con la atención del personal?", qtype="scale_1_10",
                 is_required=True, max_answer_seconds=10, max_retries=1,
                 texto_sin_acuse="¿Y con la atención del personal?"),
    ]


def test_dice_el_acuse_y_la_siguiente_va_sin_la_transicion_fija(entorno):
    # Saludo en silencio, P1 «uno, muy mal», P2 «diez».
    entorno.escuchas += [silencio(), habla(2), habla(2)]
    entorno.textos += ["Uno, muy mal, me lo devolvieron sucio.", "Diez."]
    api = Api(verdicts=[verdict(value=1.0), verdict(value=10.0)], acuses=[
        {"acuse": "Lamento mucho que se lo hayan devuelto sucio.", "banda": "baja"},
        {"acuse": "¡Qué bueno escuchar eso!", "banda": "alta"},
    ])
    d, _ = correr(guion(questions=preguntas()), api)
    dicho = " | ".join(entorno.dichas)
    assert "Lamento mucho que se lo hayan devuelto sucio." in dicho
    assert "Muy bien, gracias." not in dicho and "¿Y con la atención del personal?" in dicho
    # Le fue mal en una: antes de la despedida, la frase de empatía.
    assert dicho.index(FRASE_CIERRE_DISCONFORME.split(".")[0]) < dicho.rindex("Gracias.")
    assert api.pedidos_acuse == ["Uno, muy mal, me lo devolvieron sucio.", "Diez."]
    assert d.outcome == dialog.Outcome.COMPLETED


def test_sin_acuse_la_pregunta_conserva_su_transicion(entorno):
    entorno.escuchas += [silencio(), habla(2), habla(2)]
    entorno.textos += ["nueve", "diez"]
    correr(guion(questions=preguntas()), Api(verdicts=[verdict(), verdict()]))  # /acuse no contesta
    assert "Muy bien, gracias." in " ".join(entorno.dichas)
    assert FRASE_CIERRE_DISCONFORME not in entorno.dichas


def test_si_hay_que_repreguntar_no_se_acusa(entorno):
    entorno.escuchas += [silencio(), habla(2), habla(2)]
    entorno.textos += ["perdón, ¿quién habla?", "nueve"]
    api = Api(verdicts=[verdict(understood=False, value=None, should_retry=True), verdict()],
              acuses=[{"acuse": "¡Excelente!", "banda": "alta"}])
    correr(guion(), api)
    # La pregunta de vuelta no se acusa: el acuse es solo para la respuesta que se entendió, y se dice una vez.
    assert entorno.dichas.count("¡Excelente!") == 1 and api.pedidos_acuse[-1] == "nueve"


def test_un_ruido_corto_no_es_respuesta_se_sigue_escuchando(entorno):
    # Saludo en silencio; en P1 suena un golpe de 0,3 s (Whisper no saca nada) y después contesta.
    entorno.escuchas += [silencio(), habla(0.3), habla(1.5)]
    entorno.textos += ["", "ocho"]
    d, _ = correr(guion(), Api(verdicts=[verdict()]))
    assert "¿Me repite?" not in entorno.dichas and d.outcome == dialog.Outcome.COMPLETED


def test_en_recepcion_un_ruido_no_es_un_silencio(entorno):
    entorno.escuchas += [habla(0.3), habla(2)]
    entorno.textos += ["", "Quiero un turno"]
    api = ApiRecepcion([{"decir": "Ya le paso con Taller.", "accion": "terminar"}])
    correr(recepcion(), api)
    assert "¿Sigue ahí?" not in entorno.dichas and api.turnos == ["Quiero un turno"]


def test_dos_ruidos_seguidos_si_cuentan(entorno):
    """El reescuchar es una vez: si sigue sin decir nada, se repregunta como siempre."""
    entorno.escuchas += [silencio(), habla(0.3), habla(0.3), habla(1.5)]
    entorno.textos += ["", "", "nueve"]
    correr(guion(), Api(verdicts=[verdict(understood=False, value=None, should_retry=True), verdict()]))
    assert entorno.dichas.count("¿Me repite?") == 1


def test_lo_que_no_se_grabo_no_es_ruido():
    from app.listener import es_ruido

    assert es_ruido(Recording(pcm=b"\x00" * 4800, result=ListenResult.SPEECH, speech_ms=100), "")
    assert not es_ruido(Recording(pcm=b"", result=ListenResult.SILENCE, speech_ms=0), "")
    assert not es_ruido(Recording(pcm=b"\x00" * 4800, result=ListenResult.SPEECH, speech_ms=100), "sí")
    assert not es_ruido(Recording(pcm=b"\x00" * 32000, result=ListenResult.SPEECH, speech_ms=1500), "")
