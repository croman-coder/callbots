"""Le habla al bot por AudioSocket, sin pasar por el teléfono.

Sirve para separar dos cosas que desde afuera se ven igual: "el bot no entiende
lo que le dicen" y "al bot no le llega nada". Si por este camino sí transcribe,
la cadena de escucha está sana y el tramo roto es el RTP entrante de la troncal.

Dos modos:

    continuo   Voz sin parar toda la sesión. Prueba que el audio llega y que el
               reconocimiento anda. No sirve para medir cortes.
    turnos     Voz corta y después silencio de verdad, como una persona que
               contesta "un diez" y se calla. Es el que verifica que el bot
               detecta el fin de la respuesta en vez de esperar al tope.

Ojo con el pacing en modo turnos: el bot descarta lo que entra mientras habla,
y una pausa entre frases parece un hueco sin serlo. Por eso se espera a que
tenga la boca cerrada un rato largo antes de contestar.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import uuid

FRAME = 320  # 20 ms de PCM 8 kHz 16-bit
UMBRAL_VOZ = 400


def pico(pcm: bytes) -> int:
    return max(
        (abs(int.from_bytes(pcm[i : i + 2], "little", signed=True)) for i in range(0, len(pcm), 2)),
        default=0,
    )


def abrir(host: str = "voice-agent", puerto: int = 8090) -> socket.socket:
    s = socket.create_connection((host, puerto), timeout=15)
    s.settimeout(90)
    s.sendall(bytes([0x01]) + (16).to_bytes(2, "big") + uuid.UUID(int=0).bytes)
    return s


def modo_continuo(s: socket.socket, voz: bytes, segundos: int = 75) -> None:
    parar = threading.Event()
    enviadas = [0]

    def emisor() -> None:
        off = 0
        siguiente = time.monotonic()
        while not parar.is_set():
            ch = voz[off : off + FRAME]
            if len(ch) < FRAME:
                ch, off = ch.ljust(FRAME, b"\x00"), 0
            else:
                off += FRAME
            try:
                s.sendall(bytes([0x10]) + FRAME.to_bytes(2, "big") + ch)
            except OSError:
                return
            enviadas[0] += 1
            siguiente += 0.02
            if (d := siguiente - time.monotonic()) > 0:
                time.sleep(d)

    threading.Thread(target=emisor, daemon=True).start()
    fin = time.time() + segundos
    while time.time() < fin:
        if not leer_trama(s)[0]:
            break
    parar.set()
    time.sleep(0.3)
    print(f"enviadas {enviadas[0]} tramas de voz", flush=True)


def leer_trama(s: socket.socket):
    h = s.recv(3)
    if len(h) < 3:
        return None, None
    n = int.from_bytes(h[1:3], "big")
    p = b""
    while len(p) < n:
        t = s.recv(n - len(p))
        if not t:
            return None, None
        p += t
    return h[0], p


def esperar_boca_cerrada(s: socket.socket, quieto=2.0, tope=45) -> bool:
    """Espera a que el bot hable y después se calle de verdad."""
    fin = time.time() + tope
    hablo = False
    ultimo_sonido = time.time()
    while time.time() < fin:
        t, p = leer_trama(s)
        if t is None:
            return False
        if t != 0x10:
            continue
        if pico(p) > UMBRAL_VOZ:
            hablo = True
            ultimo_sonido = time.time()
        elif hablo and time.time() - ultimo_sonido > quieto:
            return True
    return False


def modo_turnos(s: socket.socket, voz: bytes, rondas: int = 3) -> None:
    for ronda in range(1, rondas + 1):
        if not esperar_boca_cerrada(s):
            print(f"ronda {ronda}: el bot no se calló, corto", flush=True)
            return
        print(f"ronda {ronda}: contesto y me callo", flush=True)
        arranque = time.time()
        for off in range(0, len(voz), FRAME):
            ch = voz[off : off + FRAME].ljust(FRAME, b"\x00")
            s.sendall(bytes([0x10]) + FRAME.to_bytes(2, "big") + ch)
            time.sleep(0.019)
        # Silencio real: acá es donde el bot tiene que darse cuenta de que
        # terminó. Si sigue esperando, el corte por silencio está roto.
        for _ in range(250):
            s.sendall(bytes([0x10]) + FRAME.to_bytes(2, "big") + b"\x00" * FRAME)
            time.sleep(0.019)
        print(f"  ({time.time() - arranque:.1f}s desde que empecé a contestar)", flush=True)


def main() -> None:
    modo = sys.argv[1] if len(sys.argv) > 1 else "turnos"
    voz = open("/tmp/voz.pcm", "rb").read()
    s = abrir()
    print(f"conectado · modo {modo} · respuesta de {len(voz) / 2 / 8000:.1f}s", flush=True)
    try:
        if modo == "continuo":
            modo_continuo(s, voz)
        else:
            modo_turnos(s, voz)
    finally:
        s.close()
    print("listo", flush=True)


if __name__ == "__main__":
    main()
