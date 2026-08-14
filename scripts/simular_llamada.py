#!/usr/bin/env python3
"""Simula el canal de Asterisk contra el AudioSocket del voice-agent.

Prueba la conversación completa —presentación, preguntas, reconocimiento,
cierre— sin telefonía: ni troncal, ni softphone, ni nadie atendiendo el
teléfono. Hace exactamente lo que hace el dialplan: abre un TCP, manda el UUID
de la sesión y después intercambia tramas de 20 ms.

Las respuestas del "cliente" las sintetiza el mismo Piper que usa el bot. No
son voz humana, así que esto valida el circuito y la lógica, no la precisión
del reconocimiento con gente real. Para eso hay que llamar de verdad.

Uso, desde el contenedor del voice-agent:

    docker compose exec voice-agent python /app/scripts/simular_llamada.py
    docker compose exec voice-agent python /app/scripts/simular_llamada.py --guardar /tmp/llamada.wav

Corre contra la campaña ACTIVA en modo demo (el UUID en cero, igual que la
extensión 9000): no toca Bitrix, no marca destinatarios y no guarda resultados.
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import select
import socket
import struct
import time
import uuid
import wave

# Respuestas del cliente simulado. Frases y no números sueltos a propósito:
# Piper sintetiza "ocho" en 0,4 s con un corte muy brusco y Whisper lo falla
# seguido. Un cliente real no habla así, pero el TTS sí.
RESPUESTAS = [
    "si, con gusto",
    "le doy un nueve",
    "le doy un diez",
    "le doy un ocho",
    "le doy un nueve",
    "le doy un diez",
    "todo muy bien, la atencion fue excelente",
]

DEMO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")
FRAME = 320          # 20 ms de slin 8 kHz, 16-bit
TICK = 0.02
SILENCIO = b"\x00" * FRAME
UMBRAL_RMS = 300     # arriba de esto se considera que el bot está hablando
PAUSA_PARA_RESPONDER = 0.8


async def _sintetizar() -> list[bytes]:
    from app import tts

    tts.load_voice()
    return [await tts.synthesize(t) for t in RESPUESTAS]


def simular(respuestas: list[bytes], host: str, port: int, max_seg: int) -> tuple:
    s = socket.create_connection((host, port), timeout=10)
    s.sendall(bytes([0x01]) + struct.pack(">H", 16) + DEMO_UUID.bytes)
    s.setblocking(False)
    print(f"conectado a {host}:{port} | sesión de demo")

    buf = b""
    pendiente = b""
    idx = 0
    bot_hablo = False
    sil = 0.0
    recibido = bytearray()
    t0 = time.time()
    fin = None

    while time.time() - t0 < max_seg:
        ini = time.time()

        if pendiente:
            chunk = pendiente[:FRAME].ljust(FRAME, b"\x00")
            pendiente = pendiente[FRAME:]
        else:
            chunk = SILENCIO
        try:
            s.sendall(bytes([0x10]) + struct.pack(">H", FRAME) + chunk)
        except (BrokenPipeError, ConnectionResetError):
            fin = "el voice-agent cerró la conexión"
            break

        while select.select([s], [], [], 0)[0]:
            try:
                data = s.recv(65536)
            except BlockingIOError:
                break
            if not data:
                fin = "conexión cerrada"
                break
            buf += data
        if fin:
            break

        hubo_voz = False
        while len(buf) >= 3:
            tipo, largo = buf[0], struct.unpack(">H", buf[1:3])[0]
            if len(buf) < 3 + largo:
                break
            payload = buf[3:3 + largo]
            buf = buf[3 + largo:]
            if tipo == 0x00:
                fin = "el bot colgó"
                break
            if tipo == 0x10 and payload:
                recibido.extend(payload)
                if audioop.rms(payload, 2) > UMBRAL_RMS:
                    hubo_voz = True
        if fin:
            break

        if hubo_voz:
            bot_hablo, sil = True, 0.0
        else:
            sil += TICK

        if bot_hablo and not pendiente and sil >= PAUSA_PARA_RESPONDER:
            if idx < len(respuestas):
                print(f"  [{time.time() - t0:6.1f}s] -> {RESPUESTAS[idx]!r}")
                pendiente = respuestas[idx]
                idx += 1
            bot_hablo, sil = False, 0.0

        resto = TICK - (time.time() - ini)
        if resto > 0:
            time.sleep(resto)

    s.close()
    return bytes(recibido), idx, time.time() - t0, fin


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--max-seg", type=int, default=180)
    parser.add_argument("--guardar", help="WAV con el audio que emitió el bot")
    args = parser.parse_args()

    print("sintetizando las respuestas del cliente...")
    respuestas = asyncio.run(_sintetizar())

    audio, enviadas, dur, fin = simular(respuestas, args.host, args.port, args.max_seg)

    print(f"\nfin: {fin or 'límite de tiempo'}")
    print(f"duración: {dur:.1f}s | audio del bot: {len(audio) / 2 / 8000:.1f}s")
    print(f"respuestas enviadas: {enviadas}/{len(RESPUESTAS)}")

    if args.guardar:
        with wave.open(args.guardar, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(audio)
        print(f"audio guardado en {args.guardar}")

    print("\nQué entendió el bot, en los logs del voice-agent:")
    print("  docker compose logs voice-agent | grep app.dialog")


if __name__ == "__main__":
    main()
