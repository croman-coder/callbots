"""Turnos de la conversación: cuándo se le pregunta a Claude y cuándo alcanza con la frase fija."""
from __future__ import annotations

from app import dialog
from tests.test_buzon_y_transferencia import Api, correr, entorno, guion, habla, silencio, verdict  # noqa: F401


class ApiQueCuenta(Api):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.consultas = []

    async def conversational_reply(self, **kw):
        self.consultas.append(kw)
        return "Le habla el asistente de Santa Rosa. ¿Del cero al diez, cómo lo atendieron?"


def test_si_no_dijo_nada_se_repite_sin_muletilla_ni_claude(entorno):
    # saludo, P1 en silencio, P1 de nuevo con respuesta
    entorno.escuchas += [silencio(), silencio(), habla(1)]
    entorno.textos += ["nueve"]
    api = ApiQueCuenta(verdicts=[verdict(understood=False, value=None, is_silence=True, should_retry=True), verdict()])
    d, _ = correr(guion(), api)
    assert api.consultas == []  # nada que contestar: no se gasta un segundo en Claude
    assert dialog.MULETILLA not in entorno.dichas  # «déjeme ver» antes de «no lo escuché» suena raro
    assert "¿Me repite?" in entorno.dichas and d.outcome == dialog.Outcome.COMPLETED


def test_si_pregunto_algo_contesta_claude_tapando_la_espera(entorno):
    entorno.escuchas += [silencio(), habla(1), habla(1)]
    entorno.textos += ["perdón, ¿quién habla?", "un nueve"]
    api = ApiQueCuenta(verdicts=[verdict(understood=False, value=None, should_retry=True), verdict()])
    d, _ = correr(guion(), api)
    assert len(api.consultas) == 1 and api.consultas[0]["transcript"] == "perdón, ¿quién habla?"
    assert dialog.MULETILLA in entorno.dichas and d.outcome == dialog.Outcome.COMPLETED
