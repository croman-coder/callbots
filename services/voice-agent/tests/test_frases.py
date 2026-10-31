"""El bot no habla de corrido: cada frase va por separado, con una pausa entre una y otra."""
from __future__ import annotations

import asyncio
import os

os.environ["VOICEBOX_URL"] = "http://voicebox:17493"
os.environ["VOICEBOX_PROFILE_ID"] = ""

import pytest  # noqa: E402

from app import tts  # noqa: E402
from app.config import SAMPLE_RATE, config  # noqa: E402
from app.guion import duracion_estimada, partir_frases, pausa_tras  # noqa: E402

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
    frases = partir_frases(SALUDO)
    pausas = [SAMPLE_RATE * 2 * pausa_tras(f) // 1000 for f in frases[:-1]]
    assert _frases == frases
    assert len(pcm) == 3 * 200 + sum(pausas)
    assert pcm[200 : 200 + pausas[0]] == b"\x00" * pausas[0]


def test_la_pausa_entre_frases_depende_de_como_termina():
    assert pausa_tras("Le habla Agustín, de Santa Rosa.") == 380
    assert pausa_tras("¿Tiene un minutito?") == 420
    assert pausa_tras("Déjeme ver...") == pausa_tras("Déjeme ver…") == 550
    # Más corta que los 450 ms fijos de antes: los bordes de cada frase ya traen ~110 ms.
    assert all(p < 450 + 110 for p in (pausa_tras("Hola."), pausa_tras("¿Sí?")))


def test_la_duracion_pedida_sigue_al_texto_y_las_comas():
    corta, larga = duracion_estimada("Entiendo."), duracion_estimada("Entiendo, gracias por decírmelo con franqueza.")
    assert 0.5 < corta < larga < 4.5
    # Cada coma suma una pausa.
    assert duracion_estimada("Hola, Marta, buenas") > duracion_estimada("Hola Marta buenas")
    assert duracion_estimada("") == duracion_estimada("¿?") == 0.0


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

    def teclear(self, si):
        pass


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
    assert [x for x in s.reproducido if x[0] == "pausa"] == [("pausa", pausa_tras(f)) for f in frases[:-1]]
    assert s.reproducido[0] == ("evento", SALUDO)


def test_dos_textos_seguidos_del_agente_no_salen_pegados(monkeypatch):
    """El acuse y la pregunta se dicen con dos _say: entre los dos va la pausa de fin de frase (31/10/2026)."""
    import sys
    import types

    sys.modules.setdefault("webrtcvad", types.SimpleNamespace(Vad=lambda *a: None))
    from app import dialog

    async def frase(text, params=None):
        return text.encode()

    monkeypatch.setattr(tts, "sintetizar_frase", frase)
    s = _SocketFalso()
    asyncio.run(dialog._say(s, "Uy, lamento mucho escuchar eso."))
    asyncio.run(dialog._say(s, "¿Y con la atención que recibió?"))
    pausas = [x for x in s.reproducido if x[0] == "pausa"]
    assert len(pausas) == 1 and 300 <= pausas[0][1] <= pausa_tras("Uy, lamento mucho escuchar eso.")
    # Si el cliente habló en el medio, ya pasó el tiempo: no se agrega nada.
    s.fin_de_voz = (s.fin_de_voz[0] - 5, s.fin_de_voz[1])
    asyncio.run(dialog._say(s, "Gracias."))
    assert len([x for x in s.reproducido if x[0] == "pausa"]) == 1


# ------------------------------------------------------------------ volumen parejo
import numpy as np  # noqa: E402


def _tono(amplitud: float, segundos: float = 1.0) -> bytes:
    t = np.arange(int(8000 * segundos)) / 8000
    return (np.sin(2 * np.pi * 440 * t) * amplitud * 32767).astype(np.int16).tobytes()


def _rms(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
    return float(np.sqrt(np.mean(x**2)))


def test_frases_suaves_y_fuertes_quedan_al_mismo_volumen():
    suave, fuerte = tts.nivelar(_tono(0.05)), tts.nivelar(_tono(0.6))
    assert abs(_rms(suave) - _rms(fuerte)) < 0.01
    assert abs(_rms(suave) - tts.NIVEL_RMS) < 0.01


def test_nivelar_no_satura_ni_cambia_al_repetirse():
    una = tts.nivelar(_tono(0.3), volumen=1.5)
    assert np.max(np.abs(np.frombuffer(una, dtype=np.int16))) <= tts.TECHO * 32767 + 1
    assert abs(_rms(tts.nivelar(una, volumen=1.5)) - _rms(una)) < 0.005


def test_las_pausas_no_cuentan_y_el_silencio_queda_igual():
    silencio = b"\x00\x00" * 8000
    assert tts.nivelar(silencio) == silencio
    con_pausa = _tono(0.2) + silencio
    # el silencio no baja el promedio: la parte con voz queda al nivel de destino
    voz = tts.nivelar(con_pausa)[: 16000]
    assert abs(_rms(voz) - tts.NIVEL_RMS) < 0.01


def test_el_volumen_de_la_campana_sube_o_baja_la_voz_buena():
    assert _rms(tts.nivelar(_tono(0.2), volumen=1.2)) > _rms(tts.nivelar(_tono(0.2), volumen=0.8))


# ------------------------------------------------------------------ bordes y pausas de la voz
def _ruido(segundos: float, amplitud: float = 0.003) -> bytes:
    rng = np.random.default_rng(1)
    return (rng.normal(0, amplitud, int(8000 * segundos)) * 32767).astype(np.int16).tobytes()


def _ms(pcm: bytes) -> float:
    return len(pcm) / 16


def _silencios_internos(pcm: bytes) -> list[float]:
    x = np.abs(np.frombuffer(pcm, dtype=np.int16).astype(np.float32))
    e = x[: x.size // 80 * 80].reshape(-1, 80).mean(axis=1)
    activo = e > e.max() * 0.05
    idx = np.flatnonzero(activo)
    salida, corrida = [], 0
    for v in activo[idx[0] : idx[-1] + 1]:
        if v:
            if corrida:
                salida.append(corrida * 10)
            corrida = 0
        else:
            corrida += 1
    return salida


def test_recorta_los_bordes_de_la_voz_clonada():
    # Como la voz clonada: ~190 ms antes y ~500 ms después, con su ruido de fondo (-50 dB).
    pcm = _ruido(0.19) + _tono(0.3, 1.0) + _ruido(0.5)
    salida = tts.ajustar_ritmo(pcm)
    assert 1000 + tts.MARGEN_INICIO_MS - 10 <= _ms(salida) <= 1000 + tts.MARGEN_INICIO_MS + tts.MARGEN_FIN_MS + 10
    # Arranca y termina en silencio, sin clic.
    x = np.frombuffer(salida, dtype=np.int16)
    assert abs(int(x[0])) < 200 and abs(int(x[-1])) < 200


def test_una_pausa_larga_a_la_mitad_se_acorta_y_una_corta_queda():
    pcm = _tono(0.3, 0.6) + _ruido(1.2) + _tono(0.3, 0.4) + _ruido(0.12) + _tono(0.3, 0.4)
    salida = tts.ajustar_ritmo(pcm)
    huecos = _silencios_internos(salida)
    assert len(huecos) == 2
    assert tts.PAUSA_INTERNA_MAX_MS - 30 <= huecos[0] <= tts.PAUSA_INTERNA_MAX_MS + 10  # 1,2 s → 0,3 s
    assert 100 <= huecos[1] <= 130  # la corta, igual
    assert _ms(salida) < _ms(pcm) - 850


def test_ajustar_el_ritmo_dos_veces_da_lo_mismo():
    una = tts.ajustar_ritmo(_ruido(0.3) + _tono(0.2, 0.5) + _ruido(0.9) + _tono(0.2, 0.5) + _ruido(0.3))
    assert abs(_ms(tts.ajustar_ritmo(una)) - _ms(una)) <= 10


def test_el_silencio_y_lo_muy_corto_no_se_tocan():
    assert tts.ajustar_ritmo(b"\x00\x00" * 8000) == b"\x00\x00" * 8000
    assert tts.ajustar_ritmo(b"\x05\x00" * 100) == b"\x05\x00" * 100
    assert tts.ajustar_ritmo(b"") == b""


# ------------------------------------------------------------------ velocidad sin cambiar el tono
def _frecuencia(pcm: bytes) -> float:
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    espectro = np.abs(np.fft.rfft(x * np.hanning(x.size)))
    return float(np.argmax(espectro) * 8000 / x.size)


def test_la_velocidad_cambia_la_duracion_y_no_el_tono():
    tono = _tono(0.3, 2.0)  # 440 Hz
    rapido, lento = tts.cambiar_velocidad(tono, 1.2), tts.cambiar_velocidad(tono, 0.85)
    assert abs(_ms(rapido) - 2000 / 1.2) < 20 and abs(_ms(lento) - 2000 / 0.85) < 20
    assert abs(_frecuencia(rapido) - 440) < 8 and abs(_frecuencia(lento) - 440) < 8
    # Sin cortes ni ecos que bajen el volumen a la mitad del audio.
    medio = np.frombuffer(rapido, dtype=np.int16)[800:-800].astype(np.float32) / 32768
    assert abs(float(np.sqrt(np.mean(medio**2))) - 0.3 / np.sqrt(2)) < 0.03


def test_velocidad_normal_o_audio_muy_corto_no_se_toca_y_hay_tope():
    tono = _tono(0.3, 1.0)
    assert tts.cambiar_velocidad(tono, 1.0) == tono
    assert tts.cambiar_velocidad(b"\x05\x00" * 100, 1.3) == b"\x05\x00" * 100
    assert abs(_ms(tts.cambiar_velocidad(tono, 3.0)) - 1000 / tts.VELOCIDAD_MAX) < 20
