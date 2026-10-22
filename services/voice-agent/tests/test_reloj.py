"""El reloj de la llamada es el tiempo real, no la cantidad de tramas que llegan.

Medido el 21/10/2026 en una prueba desde el navegador: el audio del micrófono llega a ráfagas (el WebSocket y el TCP
juntan varias tramas). El reloj viejo contaba por trama, y cuando una no llegaba a tiempo metía un silencio y la
contaba igual: así, cada ráfaga se contaba dos veces. El agente terminó la pregunta 1 a los 18 s cuando el audio real
duraba 31 s: escuchaba mientras la persona todavía oía el saludo, y metía silencios falsos en su voz grabada.
"""
from __future__ import annotations

import asyncio
import socket as so
import time

import pytest

from app import listener
from app.audiosocket import TYPE_AUDIO, AudioSocket
from app.config import BYTES_PER_FRAME
from app.listener import ListenResult, listen

VOZ = b"\x10\x27" * (BYTES_PER_FRAME // 2)   # trama con señal (10000)
MUDA = b"\x00" * BYTES_PER_FRAME


def _trama(payload: bytes) -> bytes:
    return bytes([TYPE_AUDIO]) + len(payload).to_bytes(2, "big") + payload


async def _par():
    """Un AudioSocket del lado del agente y el escritor del otro extremo (el navegador o Asterisk)."""
    a, b = so.socketpair()
    r1, w1 = await asyncio.open_connection(sock=a)
    _, w2 = await asyncio.open_connection(sock=b)
    s = AudioSocket(r1, w1)
    s.start_pump()
    return s, w2


async def _rafagas(w, tramas: list[bytes], por_rafaga: int = 5):
    """Manda las tramas a ráfagas: `por_rafaga` juntas cada `por_rafaga` × 20 ms (en promedio, tiempo real)."""
    for i in range(0, len(tramas), por_rafaga):
        w.write(b"".join(_trama(t) for t in tramas[i:i + por_rafaga]))
        await w.drain()
        await asyncio.sleep(0.02 * por_rafaga)


@pytest.fixture(autouse=True)
def _vad_simple(monkeypatch):
    """VAD de prueba: es voz si la trama tiene señal. El de verdad no reconoce un tono como voz."""
    class Vad:
        def __init__(self, *_):
            pass

        def is_speech(self, frame, _rate):
            return any(frame)

    monkeypatch.setattr(listener.webrtcvad, "Vad", Vad)


def test_reproducir_dura_lo_que_dura_el_audio_aunque_la_entrada_llegue_a_rafagas():
    async def prueba():
        s, w = await _par()
        entrada = asyncio.create_task(_rafagas(w, [MUDA] * 150))  # 3 s de micrófono, a ráfagas
        inicio = time.monotonic()
        await s.play(MUDA * 50)  # 1 s de voz del bot
        dura = time.monotonic() - inicio
        entrada.cancel()
        return dura

    dura = asyncio.run(prueba())
    assert 0.9 <= dura <= 1.4, f"1 s de audio se reprodujo en {dura:.2f} s"


def test_reproducir_sin_ningun_audio_de_entrada_igual_avanza_a_tiempo_real():
    async def prueba():
        s, _w = await _par()  # el otro extremo no manda nada (NAT cerrado, troncal de una vía)
        inicio = time.monotonic()
        await s.play(MUDA * 25)
        return time.monotonic() - inicio

    dura = asyncio.run(prueba())
    assert 0.45 <= dura <= 0.8


def test_la_voz_grabada_no_tiene_silencios_inventados():
    async def prueba():
        s, w = await _par()
        entrada = asyncio.create_task(_rafagas(w, [VOZ] * 50 + [MUDA] * 100))  # 1 s de voz y después silencio
        grabacion = await listen(s, max_seconds=10, silence_ms=600, no_speech_timeout=3)
        entrada.cancel()
        return grabacion

    g = asyncio.run(prueba())
    assert g.result is ListenResult.SPEECH
    tramas = [g.pcm[i:i + BYTES_PER_FRAME] for i in range(0, len(g.pcm), BYTES_PER_FRAME)]
    voz = [t for t in tramas if any(t)]
    assert len(voz) == 50  # toda la voz, ni una trama perdida
    primera, ultima = tramas.index(voz[0]), len(tramas) - 1 - tramas[::-1].index(voz[-1])
    assert all(any(t) for t in tramas[primera:ultima + 1]), "se metieron silencios en medio de la voz"


def test_nadie_habla_el_tiempo_de_espera_es_de_reloj():
    async def prueba():
        s, w = await _par()
        entrada = asyncio.create_task(_rafagas(w, [MUDA] * 200))  # silencio a ráfagas
        inicio = time.monotonic()
        g = await listen(s, max_seconds=10, no_speech_timeout=1)
        entrada.cancel()
        return g, time.monotonic() - inicio

    g, dura = asyncio.run(prueba())
    assert g.result is ListenResult.SILENCE
    assert 0.9 <= dura <= 1.4, f"esperó {dura:.2f} s en vez de 1 s"


def test_nadie_habla_y_no_llega_ningun_audio_igual_se_corta_a_tiempo():
    async def prueba():
        s, _w = await _par()
        inicio = time.monotonic()
        g = await listen(s, max_seconds=10, no_speech_timeout=1)
        return g, time.monotonic() - inicio

    g, dura = asyncio.run(prueba())
    assert g.result is ListenResult.SILENCE and 0.9 <= dura <= 1.4


def test_si_el_cliente_habla_encima_el_agente_se_calla():
    async def prueba():
        s, w = await _par()
        # El cliente arranca a hablar a los 0,4 s de una frase de 2 s.
        entrada = asyncio.create_task(_rafagas(w, [MUDA] * 20 + [VOZ] * 60))
        inicio = time.monotonic()
        cortado = await s.play(MUDA * 100, escucha=listener.Interrupcion())
        dura = time.monotonic() - inicio
        g = await listen(s, max_seconds=10, silence_ms=300, no_speech_timeout=3, previo=cortado)
        entrada.cancel()
        return cortado, dura, g

    cortado, dura, g = asyncio.run(prueba())
    assert cortado and dura < 1.2, f"siguió hablando {dura:.2f} s"
    # La respuesta grabada empieza con lo que dijo encima: no se pierde el arranque.
    assert g.result is ListenResult.SPEECH and g.pcm.startswith(b"".join(cortado))
    assert sum(1 for i in range(0, len(g.pcm), BYTES_PER_FRAME) if any(g.pcm[i:i + BYTES_PER_FRAME])) >= 55


def test_el_eco_bajo_no_corta_al_agente():
    eco = b"\x20\x03" * (BYTES_PER_FRAME // 2)  # 800: suena, pero menos del doble del piso de energía

    async def prueba():
        s, w = await _par()
        entrada = asyncio.create_task(_rafagas(w, [eco] * 60))
        cortado = await s.play(MUDA * 50, escucha=listener.Interrupcion())
        entrada.cancel()
        return cortado

    assert asyncio.run(prueba()) is None
