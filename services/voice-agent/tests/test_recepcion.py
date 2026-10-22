"""Recepción: el cliente llama, el agente conversa turno a turno y el caso se cierra siempre."""
from __future__ import annotations

import asyncio

from app import dialog
from app.listener import ListenResult, Recording
from tests.test_buzon_y_transferencia import Api, correr, entorno, guion, habla, silencio  # noqa: F401

FRASES = {
    "repetir": "¿Me lo repite?",
    "registrado": "Ya registré su consulta.",
    "sigue_ahi": "¿Sigue ahí?",
    "sin_respuesta": "No lo escucho. Que tenga buen día.",
    "tiempo": "Para no demorarlo más, ya registré lo que me contó.",
}


def recepcion(**cambios):
    return guion(modo="recepcion", frases=FRASES, questions=[], intro_script="Posventa, ¿en qué lo ayudo?", **cambios)


class ApiRecepcion(Api):
    def __init__(self, respuestas, demora=0.0, **kw):
        super().__init__(**kw)
        self.respuestas = list(respuestas)
        self.demora = demora
        self.turnos: list[str] = []
        self.fin: list = []

    async def recepcion_inicio(self, session_uuid, telefono=None):
        return 7

    async def recepcion_turno(self, caso_id, texto):
        self.turnos.append(texto)
        await asyncio.sleep(self.demora)
        return self.respuestas.pop(0)

    async def recepcion_fin(self, caso_id, causa):
        self.fin.append((caso_id, causa))


def test_conversa_hasta_que_se_despide(entorno):
    entorno.escuchas += [habla(2), habla(1)]
    entorno.textos += ["Encargué un espejo hace tres semanas", "No, nada más, gracias"]
    api = ApiRecepcion([
        {"decir": "Lo paso a Repuestos, le responden en 24 horas. ¿Algo más?", "accion": "derivar"},
        {"decir": "Que tenga buen día.", "accion": "terminar"},
    ])
    d, _ = correr(recepcion(), api)
    assert api.turnos == ["Encargué un espejo hace tres semanas", "No, nada más, gracias"]
    assert entorno.dichas[0] == "Posventa, ¿en qué lo ayudo?" and entorno.dichas[-1] == "Que tenga buen día."
    assert d.outcome == dialog.Outcome.COMPLETED and api.fin == [(7, "se_despidio")]
    assert api.finales == []  # no es una encuesta: no se reporta como llamada saliente


def test_respuesta_rapida_sin_muletilla_y_lenta_con_muletilla(entorno):
    entorno.escuchas += [habla(1)]
    entorno.textos += ["¿Hasta qué hora abren el sábado?"]
    correr(recepcion(), ApiRecepcion([{"decir": "Hasta el mediodía.", "accion": "terminar"}]))
    assert dialog.MULETILLA not in entorno.dichas

    entorno.dichas.clear()
    entorno.escuchas += [habla(1)]
    entorno.textos += ["¿Y el domingo?"]
    lenta = ApiRecepcion([{"decir": "El domingo está cerrado.", "accion": "terminar"}], demora=1.2)
    correr(recepcion(), lenta)
    # La muletilla va mientras se espera, antes de la respuesta, nunca después.
    assert entorno.dichas.index(dialog.MULETILLA) < entorno.dichas.index("El domingo está cerrado.")


def test_dos_silencios_seguidos_cierran_amable(entorno):
    entorno.escuchas += [silencio(), silencio()]
    api = ApiRecepcion([])
    d, _ = correr(recepcion(), api)
    assert entorno.dichas[1:] == ["¿Sigue ahí?", "No lo escucho. Que tenga buen día."]
    assert api.turnos == [] and api.fin == [(7, "sin_respuesta")] and d.hangup_cause == "sin_respuesta"


def test_si_corta_a_la_mitad_el_caso_se_cierra_igual(entorno):
    entorno.escuchas += [habla(2), Recording(pcm=b"", result=ListenResult.HANGUP, speech_ms=0)]
    entorno.textos += ["El auto hace un ruido raro"]
    api = ApiRecepcion([{"decir": "¿Me dice su nombre?", "accion": "seguir"}])
    d, _ = correr(recepcion(), api)
    assert api.fin and api.fin[0][0] == 7 and "cortó" in api.fin[0][1]
    assert api.finales == []


def test_sin_api_dice_que_registro_y_termina(entorno):
    entorno.escuchas += [habla(1)]
    entorno.textos += ["Quiero un turno"]
    api = ApiRecepcion([{"decir": "", "accion": "terminar"}])
    correr(recepcion(), api)
    assert entorno.dichas[-1] == "Ya registré su consulta."


def test_frases_de_recepcion_se_precalientan():
    script = recepcion()
    assert dialog.MULETILLA in script.all_prompts and "¿Sigue ahí?" in script.all_prompts
    assert "¿Del cero al diez?" not in script.all_prompts


def test_consultar_dice_que_espera_y_contesta_con_el_dato(entorno):
    entorno.escuchas += [habla(2)]
    entorno.textos += ["Quiero saber si llegó mi repuesto, mi cédula es 1234567"]

    class ApiConsulta(ApiRecepcion):
        async def recepcion_continuar(self, caso_id):
            self.turnos.append("(continuar)")
            return {"decir": "Su espejo llega aproximadamente el 20 de octubre.", "accion": "terminar"}

    api = ApiConsulta([{"decir": "Ya lo busco en el sistema, un momento.", "accion": "consultar"}])
    d, _ = correr(recepcion(), api)
    dicho = " ".join(entorno.dichas)
    assert api.turnos[-1] == "(continuar)"
    assert dicho.index("Ya lo busco en el sistema") < dicho.index("llega aproximadamente")
    assert d.outcome == dialog.Outcome.COMPLETED


def test_si_retoma_despues_de_la_pausa_se_junta_todo(entorno):
    # «…nadie me avisó nada.» (pausa larga: la escucha corta) «Estoy bastante molesto.»: la segunda parte llega
    # mientras se transcribe la primera. Se sigue escuchando y se transcribe todo junto, en un solo turno.
    entorno.escuchas += [habla(2), habla(1)]
    entorno.continuaciones += [[b"\x02\x00" * 160]]
    entorno.textos += ["Encargué un parabrisas y nadie me avisó nada. Estoy bastante molesto."]
    api = ApiRecepcion([{"decir": "Lo paso a Repuestos.", "accion": "terminar"}])
    correr(recepcion(), api)
    assert api.turnos == ["Encargué un parabrisas y nadie me avisó nada. Estoy bastante molesto."]
    assert len(entorno.transcriptos[-1]) == (16000 * 2) + 320 + 16000  # lo primero + el arranque + lo que siguió


def test_voz_mientras_detecta_que_retoma(monkeypatch):
    from app import listener
    from app.config import BYTES_PER_FRAME

    class Vad:  # el detector real no es lo que se prueba acá: voz = señal fuerte
        def __init__(self, *a):
            pass

        def is_speech(self, frame, rate):
            return listener.pico_de(frame) > 1000

    monkeypatch.setattr(listener.webrtcvad, "Vad", Vad)

    class Fuente:
        def __init__(self, tramas):
            self.tramas = list(tramas)

        async def siguiente_trama(self, espera):
            await asyncio.sleep(0)
            return self.tramas.pop(0) if self.tramas else None

    import math

    voz = b"".join(int(9000 * math.sin(i / 3)).to_bytes(2, "little", signed=True) for i in range(BYTES_PER_FRAME // 2))
    nada = b"\x00" * BYTES_PER_FRAME

    async def probar(tramas):
        async def tarda():
            await asyncio.sleep(0.5)
        tarea = asyncio.ensure_future(tarda())
        r = await listener.voz_mientras(Fuente(tramas), tarea)
        tarea.cancel()
        return r

    siguio = asyncio.run(probar([nada] * 5 + [voz] * 20))
    assert siguio is not None and siguio[-1] == voz
    assert asyncio.run(probar([nada] * 30)) is None
    # Habla con cortes (consonantes): no hay 200 ms seguidos, pero sí 200 ms dentro de 300.
    assert asyncio.run(probar([nada] * 5 + [voz, voz, voz, nada] * 6)) is not None
    # Un golpe suelto no es que retomó.
    assert asyncio.run(probar([nada] * 5 + [voz, voz] + [nada] * 20)) is None


def test_voz_mientras_espera_a_confirmar_si_ya_empezo_a_sonar(monkeypatch):
    """La transcripción terminó justo cuando el cliente retomaba: se espera un poco para confirmarlo."""
    from app import listener
    from app.config import BYTES_PER_FRAME

    class Vad:
        def __init__(self, *a):
            pass

        def is_speech(self, frame, rate):
            return listener.pico_de(frame) > 1000

    monkeypatch.setattr(listener.webrtcvad, "Vad", Vad)
    voz, nada = b"\x10\x27" * (BYTES_PER_FRAME // 2), b"\x00" * BYTES_PER_FRAME

    class Fuente:  # una trama cada 20 ms, como llega de verdad
        def __init__(self, tramas):
            self.tramas = list(tramas)

        async def siguiente_trama(self, espera):
            await asyncio.sleep(0.02)
            return self.tramas.pop(0) if self.tramas else None

    async def probar(tramas):
        tarea = asyncio.ensure_future(asyncio.sleep(0.1))  # la transcripción termina a las ~5 tramas
        return await listener.voz_mientras(Fuente(tramas), tarea)

    assert asyncio.run(probar([nada] * 3 + [voz] * 20)) is not None  # arrancó justo al terminar
    assert asyncio.run(probar([nada] * 30)) is None  # silencio: no se espera
