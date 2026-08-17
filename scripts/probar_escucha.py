"""Le habla al bot con voz real y continua, sin pasar por el teléfono.

Adivinar en qué momento contestar es frágil: el bot descarta lo que entra
mientras habla, y una pausa entre frases parece un hueco sin serlo. Acá se
manda voz sin parar durante toda la sesión, en un hilo aparte. Si con audio
real y constante el bot igual reporta silencio, el problema es de entrega y no
del teléfono.
"""
import socket, threading, time, uuid

FRAME = 320       # 20 ms de PCM 8 kHz 16-bit
SEGUNDOS = 75


def main() -> None:
    voz = open("/tmp/voz.pcm", "rb").read()
    s = socket.create_connection(("voice-agent", 8090), timeout=15)
    s.settimeout(90)
    s.sendall(bytes([0x01]) + (16).to_bytes(2, "big") + uuid.UUID(int=0).bytes)
    print("conectado a la sesión demo", flush=True)

    parar = threading.Event()
    enviadas = [0]

    def emisor():
        """Voz en bucle, a ritmo real, hasta que se corte."""
        off = 0
        siguiente = time.monotonic()
        while not parar.is_set():
            ch = voz[off:off + FRAME]
            if len(ch) < FRAME:
                ch = ch.ljust(FRAME, b"\x00")
                off = 0
            else:
                off += FRAME
            try:
                s.sendall(bytes([0x10]) + FRAME.to_bytes(2, "big") + ch)
            except OSError:
                return
            enviadas[0] += 1
            siguiente += 0.02
            dormir = siguiente - time.monotonic()
            if dormir > 0:
                time.sleep(dormir)

    hilo = threading.Thread(target=emisor, daemon=True)
    hilo.start()

    recibidas = 0
    fin = time.time() + SEGUNDOS
    try:
        while time.time() < fin:
            h = s.recv(3)
            if len(h) < 3:
                break
            n = int.from_bytes(h[1:3], "big")
            p = b""
            while len(p) < n:
                t = s.recv(n - len(p))
                if not t:
                    break
                p += t
            if h[0] == 0x10:
                recibidas += 1
    except OSError:
        pass

    parar.set()
    time.sleep(0.3)
    s.close()
    print(f"enviadas {enviadas[0]} tramas de voz · recibidas {recibidas} del bot", flush=True)


if __name__ == "__main__":
    main()
