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


def test_si_contesta_encima_de_la_pregunta_se_escucha_desde_ahi(entorno):
    from tests.test_buzon_y_transferencia import Socket

    s = Socket()
    arranque = [b"\x05\x00" * 160] * 5
    s.interrumpe = [arranque]  # contesta mientras el agente todavía hace la pregunta
    entorno.escuchas += [silencio(), habla(1)]
    entorno.textos += ["ocho"]
    d, _ = correr(guion(), Api(verdicts=[verdict()]), socket=s)
    assert entorno.previos[-1] == arranque and d.outcome == dialog.Outcome.COMPLETED
    # Ni la pausa de después de la pregunta: ya está hablando.
    pregunta = next(i for i, h in enumerate(s.hechos) if h[0] == "voz" and h[1] == "¿Del cero al diez?".encode())
    assert s.hechos[pregunta + 1][0] != "pausa"


def test_en_recepcion_se_puede_interrumpir_al_agente_pero_no_la_despedida(entorno):
    from tests.test_buzon_y_transferencia import Socket

    s = Socket()
    arranque = [b"\x05\x00" * 160] * 5
    s.interrumpe = [arranque]  # el saludo no se interrumpe (ni lo mira); la primera respuesta, sí
    entorno.escuchas += [habla(2), habla(2)]
    entorno.textos += ["Quiero un turno", "El lunes"]
    api = ApiRecepcion([{"decir": "Claro. ¿Para qué día?", "accion": "seguir"},
                        {"decir": "Listo, que tenga buen día.", "accion": "terminar"}])
    correr(recepcion(), api, socket=s)
    assert entorno.previos == [None, arranque] and api.turnos == ["Quiero un turno", "El lunes"]


def test_con_teclado_teclea_mientras_piensa_y_calla_al_hablar(entorno):
    from tests.test_buzon_y_transferencia import Socket

    s = Socket()
    entorno.escuchas += [habla(2)]
    entorno.textos += ["Quiero un turno"]
    api = ApiRecepcion([{"decir": "Claro, ya le busco uno.", "accion": "terminar"}], demora=1.2)
    correr(recepcion(teclado=True), api, socket=s)
    teclas = [h[1] for h in s.hechos if h[0] == "teclado"]
    assert True in teclas and teclas[-1] is False  # tecleó mientras pensaba y se calló para hablar
    # Con teclado no hace falta la muletilla para una espera de 1,2 s: el tecleo ya dice que está buscando.
    assert dialog.MULETILLA not in entorno.dichas


def test_el_ambiente_se_mezcla_con_lo_que_sale():
    import asyncio
    import socket as so

    import numpy as np

    from app import ambiente
    from app.audiosocket import AudioSocket

    async def prueba():
        a, b = so.socketpair()
        r1, w1 = await asyncio.open_connection(sock=a)
        r2, _ = await asyncio.open_connection(sock=b)
        s = AudioSocket(r1, w1)
        s.activar_ambiente(ambiente.oficina(), ambiente.teclado())
        await asyncio.sleep(0.3)  # el agente no dice nada: el relleno manda el ambiente igual
        recibido = await asyncio.wait_for(r2.read(20000), 1)
        await s.close()
        return recibido

    datos = asyncio.run(prueba())
    tramas = []
    while len(datos) >= 3:
        largo = int.from_bytes(datos[1:3], "big")
        tramas.append(datos[3:3 + largo])
        datos = datos[3 + largo:]
    audio = np.frombuffer(b"".join(t for t in tramas if len(t) == 320), dtype=np.int16)
    assert len(tramas) >= 8 and np.abs(audio).max() > 0  # salió ambiente, no silencio digital
