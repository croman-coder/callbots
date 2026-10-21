"""El «¿tiene un minutito?»: lo que contesta el cliente se escucha y cambia lo que hace el agente."""
from __future__ import annotations

from app import dialog
from app.api_client import FRASE_LLAMAR_DESPUES
from tests.test_buzon_y_transferencia import Api, correr, entorno, guion, habla, silencio, verdict  # noqa: F401


def dicho(entorno) -> str:
    """Todo lo que dijo el agente, de corrido (lo dice partido en oraciones)."""
    return " ".join(entorno.dichas)


class ApiSaludo(Api):
    def __init__(self, decisiones, **kw):
        super().__init__(**kw)
        self.decisiones = list(decisiones)
        self.saludos = []

    async def greeting(self, session_uuid, transcript):
        self.saludos.append(transcript)
        return self.decisiones.pop(0)


def test_si_dale_arranca_la_encuesta(entorno):
    entorno.escuchas += [habla(1), habla(1)]
    entorno.textos += ["Sí, dale", "nueve"]
    api = ApiSaludo([{"accion": "seguir"}], verdicts=[verdict()])
    d, _ = correr(guion(), api)
    assert api.saludos == ["Sí, dale"] and d.outcome == dialog.Outcome.COMPLETED


def test_ahora_no_puedo_corta_amable_y_queda_para_otro_momento(entorno):
    entorno.escuchas += [habla(1.5)]
    entorno.textos += ["Ahora no puedo, estoy manejando"]
    api = ApiSaludo([{"accion": "reprogramar"}])
    d, _ = correr(guion(), api)
    assert dicho(entorno).endswith(FRASE_LLAMAR_DESPUES)
    assert "¿Del cero al diez?" not in entorno.dichas  # no se le hace ninguna pregunta
    # parcial sin respuestas: la API lo vuelve a llamar más tarde (no es una baja)
    assert d.outcome == dialog.Outcome.PARTIAL and d.hangup_cause == "pidio_llamar_despues"
    assert api.finales[0]["hangup_cause"] == "pidio_llamar_despues"


def test_no_me_llamen_dice_la_frase_de_baja_y_corta(entorno):
    entorno.escuchas += [habla(1)]
    entorno.textos += ["No me llamen más"]
    api = ApiSaludo([{"accion": "baja"}])
    d, _ = correr(guion(), api)
    assert entorno.dichas[-1] == "Entiendo." and d.hangup_cause == "pidio_no_llamar"
    assert "¿Del cero al diez?" not in entorno.dichas


def test_una_pregunta_se_contesta_y_se_vuelve_a_escuchar(entorno):
    entorno.escuchas += [habla(1), habla(1), habla(1)]
    entorno.textos += ["¿Quién habla?", "Ah, bueno, dale", "nueve"]
    respuesta = "Le habla el asistente de Santa Rosa, por su visita al taller. ¿Tiene un minuto?"
    api = ApiSaludo([{"accion": "responder", "respuesta": respuesta}, {"accion": "seguir"}], verdicts=[verdict()])
    d, _ = correr(guion(), api)
    assert dialog.MULETILLA in entorno.dichas and respuesta in dicho(entorno)  # la muletilla tapa la espera
    assert api.saludos == ["¿Quién habla?", "Ah, bueno, dale"] and d.outcome == dialog.Outcome.COMPLETED


def test_no_se_queda_preguntando_en_el_saludo(entorno):
    # Pregunta dos veces: la segunda no se vuelve a contestar, se arranca con la encuesta.
    entorno.escuchas += [habla(1), habla(1), habla(1)]
    entorno.textos += ["¿Quién habla?", "¿Y para qué?", "nueve"]
    r = "Le habla el asistente. ¿Tiene un minuto?"
    api = ApiSaludo([{"accion": "responder", "respuesta": r}, {"accion": "responder", "respuesta": r}], verdicts=[verdict()])
    d, _ = correr(guion(), api)
    assert dicho(entorno).count("Le habla el asistente.") == 1 and d.outcome == dialog.Outcome.COMPLETED


def test_pide_un_asesor_al_saludar_y_se_transfiere(entorno):
    entorno.escuchas += [habla(1)]
    entorno.textos += ["Quiero hablar con un asesor"]
    api = ApiSaludo([{"accion": "asesor"}])
    d, _ = correr(guion(transfer_enabled=True, transfer_script="Un momento."), api)
    assert api.transferencias == 1 and d.outcome == dialog.Outcome.TRANSFERRED


def test_si_no_dice_nada_arranca_igual(entorno):
    entorno.escuchas += [silencio(), habla(1)]
    entorno.textos += ["nueve"]
    api = ApiSaludo([], verdicts=[verdict()])
    d, _ = correr(guion(), api)
    assert api.saludos == [] and d.outcome == dialog.Outcome.COMPLETED
