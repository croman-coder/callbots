"""El bot no habla de corrido: cada frase va por separado, con una pausa entre una y otra."""
from __future__ import annotations

import asyncio
import os

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import SAMPLE_RATE, config  # noqa: E402
from app.guion import partir_frases  # noqa: E402

SALUDO = (
    "¡Hola, Marta! Le habla el asistente del taller de Santa Rosa. "
    "Queríamos saber cómo le fue en su última visita. ¿Tiene un minutito para unas preguntas cortas?"
)


def test_el_saludo_se_parte_en_frases():
    assert partir_frases(SALUDO) == [
        "¡Hola, Marta! Le habla el asistente del taller de Santa Rosa.",
        "Queríamos saber cómo le fue en su última visita.",
        "¿Tiene un minutito para unas preguntas cortas?",
    ]


def test_una_frase_corta_se_pega_a_la_siguiente():
    assert partir_frases("Perfecto. ¿Y con el plazo de entrega?") == ["Perfecto. ¿Y con el plazo de entrega?"]
    assert partir_frases("Hola.") == ["Hola."]
    assert partir_frases("  ") == []


def test_la_pregunta_final_va_despues_de_una_pausa():
    texto = "Son unas preguntas cortas, del cero al diez. ¿Tiene un minutito?"
    assert partir_frases(texto) == ["Son unas preguntas cortas, del cero al diez.", "¿Tiene un minutito?"]


def test_partir_una_frase_ya_partida_da_lo_mismo():
    # El audio se cachea por frase y warm_up vuelve a partir lo que recibe.
    for frase in partir_frases(SALUDO):
        assert partir_frases(frase) == [frase]


@pytest.fixture
def _frases(monkeypatch, tmp_path):
    monkeypatch.setattr(tts, "_memory_cache", {})
    object.__setattr__(config, "tts_cache_dir", str(tmp_path))
    dichas = []

    async def una(text, timeout, params):
        dichas.append(text)
        return b"\x01\x00" * 100

    monkeypatch.setattr(tts, "_sintetizar_frase", una)
    return dichas


def test_synthesize_une_las_frases_con_una_pausa(_frases):
    pcm = asyncio.run(tts.synthesize(SALUDO))
    pausa = SAMPLE_RATE * 2 * tts.PAUSA_ENTRE_FRASES_MS // 1000
    assert _frases == partir_frases(SALUDO)
    assert pcm.count(b"\x00" * pausa) == 2
    assert len(pcm) == 3 * 200 + 2 * pausa


def test_synthesize_de_una_sola_frase_no_agrega_pausa(_frases):
    pcm = asyncio.run(tts.synthesize("Gracias por su tiempo, que tenga un buen día."))
    assert len(pcm) == 200


# ------------------------------------------------------------------ transcripción en vivo
class _Escritor:
    def __init__(self):
        self.datos = b""

    def write(self, b):
        self.datos += b

    async def drain(self):
        pass

    def get_extra_info(self, _):
        return ("127.0.0.1", 1)


def _socket():
    from app.audiosocket import AudioSocket

    return AudioSocket(None, _Escritor())


def test_la_transcripcion_en_vivo_solo_sale_en_pruebas_del_navegador():
    s = _socket()
    asyncio.run(s.send_event("agente", "Hola"))
    assert s._writer.datos == b""  # llamada real: Asterisk no recibe nada raro

    s.eventos = True
    asyncio.run(s.send_event("cliente", "Sí, dígame"))
    d = s._writer.datos
    assert d[0] == 0x20 and int.from_bytes(d[1:3], "big") == len(d) - 3
    assert "Sí, dígame".encode() in d and b'"cliente"' in d


def test_un_evento_vacio_no_se_manda():
    s = _socket()
    s.eventos = True
    asyncio.run(s.send_event("cliente", ""))
    assert s._writer.datos == b""


# ------------------------------------------------------------------ frases preparadas de antemano
class _SocketFalso:
    eventos = True

    def __init__(self):
        self.reproducido = []

    async def send_event(self, rol, texto):
        self.reproducido.append(("evento", texto))

    async def play(self, pcm):
        self.reproducido.append(("voz", pcm))

    async def play_silence(self, ms):
        self.reproducido.append(("pausa", ms))


def test_el_dialogo_prepara_la_frase_siguiente_mientras_suena_la_actual(monkeypatch):
    import sys
    import types

    # El diálogo importa el detector de voz (webrtcvad), que no hace falta para esta prueba.
    sys.modules.setdefault("webrtcvad", types.SimpleNamespace(Vad=lambda *a: None))
    from app import dialog

    pedidas = []

    async def frase(text, params=None):
        pedidas.append(text)
        return text.encode()

    monkeypatch.setattr(tts, "sintetizar_frase", frase)
    s = _SocketFalso()
    asyncio.run(dialog._say(s, SALUDO))
    frases = partir_frases(SALUDO)
    assert pedidas == frases
    voces = [x[1] for x in s.reproducido if x[0] == "voz"]
    assert voces == [f.encode() for f in frases]
    assert [x for x in s.reproducido if x[0] == "pausa"] == [("pausa", tts.PAUSA_ENTRE_FRASES_MS)] * (len(frases) - 1)
    assert s.reproducido[0] == ("evento", SALUDO)
