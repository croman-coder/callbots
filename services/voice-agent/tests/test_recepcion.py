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
