"""Buzón de voz y transferencia a un asesor: qué hace el diálogo, con la escucha y la API simuladas."""
from __future__ import annotations

import asyncio
import os
import sys
import types

# Igual que los otros archivos: `config` se arma al importar y tiene que ver estas variables.
os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

sys.modules.setdefault("webrtcvad", types.SimpleNamespace(Vad=lambda *a: None))

from app import buzon, dialog, stt, tts  # noqa: E402
from app.api_client import AnswerVerdict, Question, Script  # noqa: E402
from app.audiosocket import AudioSocketClosed  # noqa: E402
from app.listener import ListenResult, Recording  # noqa: E402

# ------------------------------------------------------------------ detector puro


@pytest.mark.parametrize(
    "texto",
    [
        "Hola, has llegado al buzón de voz de Carlos, deje su mensaje después del tono",
        "El número al que usted llama no está disponible en este momento",
        "El abonado se encuentra fuera del área de cobertura",
        "Después del pitido deje su mensaje",
    ],
)
def test_frases_de_contestador(texto):
    assert buzon.dice_buzon(texto)


@pytest.mark.parametrize(
    "texto",
    ["¿Hola?", "Hola, buenas tardes", "Aló, ¿quién habla?", "El señor no está disponible, ¿quién lo llama?", "No puedo atenderlo ahora"],
)
def test_una_persona_nunca_se_toma_por_buzon(texto):
    assert not buzon.dice_buzon(texto)


def test_hablar_sin_pausa_todo_el_sondeo_es_una_maquina():
    assert buzon.es_buzon("timeout", "")
    assert not buzon.es_buzon("speech", "hola")  # saludó y esperó: una persona
    assert not buzon.es_buzon("silence", "")  # nadie habló: una persona esperando, no una máquina


# ------------------------------------------------------------------ diálogo


class Socket:
    session_uuid = "u"

    def __init__(self):
        self.hechos: list = []

    async def send_event(self, rol, texto):
        self.hechos.append(("evento", rol, texto))

    async def play(self, pcm, drain_input=True):
        self.hechos.append(("voz", pcm))

    async def play_silence(self, ms):
        self.hechos.append(("pausa", ms))


class Api:
    def __init__(self, transfer_ok=True, verdicts=None):
        self.transfer_ok = transfer_ok
        self.verdicts = list(verdicts or [])
        self.transferencias = 0
        self.finales: list = []

    async def transfer(self, session_uuid):
        self.transferencias += 1
        return self.transfer_ok

    async def submit_answer(self, **kw):
        return self.verdicts.pop(0)

    async def conversational_reply(self, **kw):
        return None

    async def session_finished(self, **kw):
        self.finales.append(kw)


def guion(**cambios) -> Script:
    base = dict(
        session_uuid="u", call_id=1, demo=False, campaign_id=1, campaign_name="Posventa", contact_name="Marta",
        intro_script="Hola, buenas tardes. ¿Tiene un minuto?", outro_script="Gracias.", fallback_script="¿Me repite?",
        optout_script="Entiendo.", voice_speed=1.0, voice_pitch=1.0, voice_expressiveness=0.667, voice_volume=1.0,
        voice_profile_id="piper",
        questions=[Question(id=1, position=1, text="¿Del cero al diez?", qtype="scale_1_10", is_required=True, max_answer_seconds=10, max_retries=1)],
    )
    base.update(cambios)
    return Script(**base)


def verdict(**cambios) -> AnswerVerdict:
    base = dict(understood=True, value=9.0, is_optout=False, is_silence=False, should_retry=False, wants_transfer=False)
    base.update(cambios)
    return AnswerVerdict(**base)


@pytest.fixture
def entorno(monkeypatch):
    """Escucha y transcripción guionadas: cada `listen` devuelve lo próximo de la lista."""
    dichas: list[str] = []
    escuchas: list[Recording] = []
    textos: list[str] = []

    async def frase(text, params=None):
        dichas.append(text)
        return text.encode()

    async def escuchar(socket, **kw):
        r = escuchas.pop(0)
        if r.result is ListenResult.HANGUP:
            return r
        return r

    async def transcribir(pcm):
        return types.SimpleNamespace(text=textos.pop(0), confidence=0.9)

    monkeypatch.setattr(tts, "sintetizar_frase", frase)
    monkeypatch.setattr(dialog, "listen", escuchar)
    monkeypatch.setattr(stt, "transcribe", transcribir)
    return types.SimpleNamespace(dichas=dichas, escuchas=escuchas, textos=textos)


def habla(segundos: float, resultado=ListenResult.SPEECH) -> Recording:
    return Recording(pcm=b"\x01\x00" * int(8000 * segundos), result=resultado, speech_ms=int(segundos * 1000))


def silencio() -> Recording:
    return Recording(pcm=b"", result=ListenResult.SILENCE, speech_ms=0)


def correr(guion_, api, socket=None):
    s = socket or Socket()
    d = dialog.SurveyDialog(s, api, guion_)
    asyncio.run(d.run())
    return d, s


# --- buzón


def test_sin_configurar_no_se_escucha_antes_de_saludar(entorno):
    # Escucha 1: el «sí, dígame» tras el saludo. Escucha 2: la respuesta. Nada de sondeo.
    entorno.escuchas += [silencio(), habla(1)]
    entorno.textos += ["nueve"]
    api = Api(verdicts=[verdict()])
    d, _ = correr(guion(), api)
    assert d.outcome == dialog.Outcome.COMPLETED and entorno.escuchas == []


def test_buzon_colgar_corta_sin_saludar(entorno):
    entorno.escuchas += [habla(5, ListenResult.TIMEOUT)]
    entorno.textos += ["Hola, has llegado al buzón de voz de Carlos"]
    api = Api()
    d, s = correr(guion(voicemail_action="colgar"), api)
    assert d.outcome == dialog.Outcome.VOICEMAIL and d.hangup_cause == "buzon_colgo"
    assert entorno.dichas == [] and not [h for h in s.hechos if h[0] == "voz"]
    assert api.finales[0]["outcome"] == "voicemail" and api.finales[0]["hangup_cause"] == "buzon_colgo"


def test_buzon_mensaje_espera_el_tono_y_lo_deja(entorno):
    entorno.escuchas += [
        habla(5, ListenResult.TIMEOUT),  # sondeo: habla largo
        habla(4),  # sigue el saludo y termina con una pausa larga: ahí suena el tono
    ]
    entorno.textos += ["deje su mensaje después del tono"]
    api = Api()
    d, s = correr(
        guion(voicemail_action="mensaje", voicemail_script="Hola {nombre}, lo volvemos a llamar. Gracias."), api
    )
    assert d.outcome == dialog.Outcome.VOICEMAIL and d.hangup_cause == "buzon_mensaje"
    assert entorno.dichas == ["Hola Marta, lo volvemos a llamar. Gracias."]
    # Primero espera el tono (silencio), recién después habla.
    orden = [h[0] for h in s.hechos]
    assert orden.index("pausa") < orden.index("voz")
    assert ("pausa", buzon.ESPERA_TONO_MS) in s.hechos


def test_una_persona_que_saluda_corto_sigue_la_encuesta(entorno):
    entorno.escuchas += [habla(1.5), silencio(), habla(1)]  # «¿aló?», «sí dígame» (silencio), respuesta
    entorno.textos += ["aló", "nueve"]
    api = Api(verdicts=[verdict()])
    d, _ = correr(guion(voicemail_action="colgar"), api)
    assert d.outcome == dialog.Outcome.COMPLETED and d.hangup_cause is None


def test_nadie_habla_al_atender_se_saluda_igual(entorno):
    entorno.escuchas += [silencio(), silencio(), habla(1)]
    entorno.textos += ["nueve"]
    d, _ = correr(guion(voicemail_action="colgar"), Api(verdicts=[verdict()]))
    assert d.outcome == dialog.Outcome.COMPLETED


def test_en_una_prueba_desde_el_navegador_no_se_busca_buzon(entorno):
    entorno.escuchas += [silencio(), habla(1)]
    entorno.textos += ["nueve"]
    d, _ = correr(guion(voicemail_action="colgar", demo=True), Api(verdicts=[verdict()]))
    assert d.outcome == dialog.Outcome.COMPLETED and entorno.escuchas == []


def test_el_contestador_que_cuelga_a_mitad_del_saludo_no_rompe_nada(entorno):
    entorno.escuchas += [habla(5, ListenResult.TIMEOUT), Recording(pcm=b"", result=ListenResult.HANGUP, speech_ms=0)]
    entorno.textos += ["buzón de voz"]
    api = Api()
    d, _ = correr(guion(voicemail_action="mensaje", voicemail_script="Hola."), api)
    assert api.finales and api.finales[0]["outcome"] == "voicemail"


# --- transferencia


def test_pide_un_asesor_y_se_transfiere(entorno):
    entorno.escuchas += [silencio(), habla(2)]
    entorno.textos += ["quiero hablar con un asesor"]
    api = Api(verdicts=[verdict(understood=False, value=None, wants_transfer=True)])
    d, s = correr(guion(transfer_enabled=True, transfer_script="Claro, un momento que lo comunico."), api)
    assert api.transferencias == 1
    assert d.outcome == dialog.Outcome.TRANSFERRED and d.hangup_cause == "transferida"
    assert "Claro, un momento que lo comunico." in entorno.dichas
    assert "¿Del cero al diez?" in entorno.dichas and "Gracias." not in entorno.dichas  # no sigue ni se despide
    assert api.finales[0]["outcome"] == "transferred"


def test_si_la_transferencia_falla_se_explica_y_sigue_la_encuesta(entorno):
    entorno.escuchas += [silencio(), habla(2), habla(1)]
    entorno.textos += ["quiero hablar con un asesor", "nueve"]
    api = Api(transfer_ok=False, verdicts=[verdict(understood=False, value=None, wants_transfer=True), verdict()])
    d, _ = correr(guion(transfer_enabled=True, transfer_script="Un momento."), api)
    assert d.outcome != dialog.Outcome.TRANSFERRED
    assert any("no puedo comunicarlo" in t for t in entorno.dichas)
    assert d.questions_asked == 1 and d.hangup_cause == "transferencia_fallida"


def test_sin_asesor_cargado_el_pedido_no_hace_nada_especial(entorno):
    entorno.escuchas += [silencio(), habla(2)]
    entorno.textos += ["quiero hablar con un asesor"]
    api = Api(verdicts=[verdict(wants_transfer=True)])
    d, _ = correr(guion(transfer_enabled=False), api)
    assert api.transferencias == 0 and d.outcome == dialog.Outcome.COMPLETED


def test_en_una_prueba_la_transferencia_se_simula(entorno):
    entorno.escuchas += [silencio(), habla(2)]
    entorno.textos += ["pasame con una persona"]
    api = Api(verdicts=[verdict(understood=False, value=None, wants_transfer=True)])
    d, s = correr(guion(demo=True, transfer_enabled=True, transfer_script="Un momento."), api)
    assert api.transferencias == 0  # no se marca a nadie
    assert any(h[0] == "evento" and "se le pasa la llamada al asesor" in h[2] for h in s.hechos)


def test_las_frases_nuevas_se_precalientan():
    g = guion(voicemail_action="mensaje", voicemail_script="Dejo mi mensaje.", transfer_enabled=True, transfer_script="Un momento.")
    assert "Dejo mi mensaje." in g.all_prompts and "Un momento." in g.all_prompts
    assert any("no puedo comunicarlo" in t for t in g.all_prompts)
    apagado = guion(voicemail_script="No se dice.", transfer_script="Tampoco.")
    assert "No se dice." not in apagado.all_prompts and "Tampoco." not in apagado.all_prompts
