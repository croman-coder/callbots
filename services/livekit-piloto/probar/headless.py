"""Cliente automático del piloto: entra a una sala como una persona y contesta la encuesta con grabaciones humanas reales.

Mide lo que importa de una conversación: cuánto tarda el agente en empezar a hablar después de que la persona termina (latencia de
turno) y qué entendió de cada respuesta (las transcripciones que LiveKit manda por el tema `lk.transcription`). Guarda el audio del agente.

    python -m probar.headless --audios 2026-10-24:p06,2026-08-14:p01,2026-08-14:p03
Cada audio es «día:pNN» dentro de /audios (las grabaciones de Callbot: audios de 8 kHz de personas de verdad).
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import secrets
import time
import wave

import numpy as np
import soxr
from livekit import api, rtc

TASA = 24000
MUESTRAS_10MS = TASA // 100
UMBRAL = 0.01  # rms a partir del cual el agente «habla»


def cargar(ruta: str) -> np.ndarray:
    w = wave.open(ruta)
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    sr = w.getframerate()
    w.close()
    y = soxr.resample(x, sr, TASA) if sr != TASA else x
    # Las grabaciones saturadas (pico 1,0) se pasan de la escala al subirlas a 24 kHz y se distorsionan al cortarlas; se baja el pico a 0,8.
    # Las que son flojas (pico 0,07: una persona con mala línea) se dejan como están: el agente tiene que poder con ellas.
    pico = float(np.abs(y).max())
    return y * (0.8 / pico) if pico > 0.8 else y


def resolver(etiqueta: str) -> str:
    """«día:pNN» o «día:pNN#k» (la k-ésima grabación de ese día con ese número, cuando hubo más de una llamada)."""
    dia_p, _, k = etiqueta.partition("#")
    dia, p = dia_p.split(":")
    hallados = sorted(glob.glob(f"/audios/{dia}/*_{p}.wav"))
    if not hallados:
        raise SystemExit(f"no hay audio {etiqueta} en /audios/{dia}")
    return hallados[int(k) if k else 0]


async def main(a: argparse.Namespace) -> None:
    sala_nombre = f"prueba-{secrets.token_hex(3)}"
    token = (api.AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
             .with_identity("tester").with_grants(api.VideoGrants(room_join=True, room=sala_nombre)).to_jwt())
    sala = rtc.Room()
    audio_agente: list[np.ndarray] = []
    audio_fondo: list[np.ndarray] = []  # la pista `background_audio` (fondo y muletillas): no es la voz del agente, no cuenta para la latencia
    est = {"ultimo": 0.0, "primero_tras": None, "esperando": False, "colgo": asyncio.Event()}
    transcripciones: list[dict] = []
    t0 = time.monotonic()

    async def leer_audio(track: rtc.Track) -> None:
        es_fondo = track.name == "background_audio"
        async for ev in rtc.AudioStream(track, sample_rate=TASA, num_channels=1):
            x = np.frombuffer(ev.frame.data, dtype=np.int16)
            if es_fondo:
                audio_fondo.append(x.copy())
                continue
            audio_agente.append(x.copy())
            if float(np.sqrt(np.mean((x.astype(np.float32) / 32768.0) ** 2))) > UMBRAL:
                ahora = time.monotonic()
                est["ultimo"] = ahora
                if est["esperando"] and est["primero_tras"] is None:
                    est["primero_tras"] = ahora

    @sala.on("track_subscribed")
    def _pista(track: rtc.Track, *_: object) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            asyncio.create_task(leer_audio(track))

    async def leer_texto(lector: rtc.TextStreamReader, identidad: str) -> None:
        texto = await lector.read_all()
        attrs = lector.info.attributes or {}
        transcripciones.append({"t": round(time.monotonic() - t0, 2), "quien": "tester" if identidad == "tester" else "agente",
                                "final": attrs.get("lk.transcription_final"), "texto": texto})

    sala.register_text_stream_handler("lk.transcription", lambda lector, ident: asyncio.create_task(leer_texto(lector, ident)))
    sala.on("disconnected", lambda *_: est["colgo"].set())

    await sala.connect(a.url, token)
    fuente = rtc.AudioSource(TASA, 1)
    pista = rtc.LocalAudioTrack.create_audio_track("mic", fuente)
    await sala.local_participant.publish_track(pista, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    print(f"sala {sala_nombre}: conectado, esperando el saludo del agente…")

    async def esperar_silencio_del_agente(minimo: float, tope: float) -> bool:
        """Espera a que el agente haya hablado y se calle `minimo` segundos. False si pasó `tope` sin que hable."""
        inicio = time.monotonic()
        while time.monotonic() - inicio < tope and not est["colgo"].is_set():
            if est["ultimo"] > inicio and time.monotonic() - est["ultimo"] >= minimo:
                return True
            await asyncio.sleep(0.1)
        return False

    filas = []
    if not await esperar_silencio_del_agente(1.2, 40):
        print("el agente no habló en 40 s")
    async def enviar(x: np.ndarray) -> float | None:
        """Manda el audio como un micrófono y espera a que el agente conteste. Devuelve los segundos hasta que empezó a hablar (None: no contestó)."""
        est.update({"esperando": False, "primero_tras": None})
        for i in range(0, len(x), MUESTRAS_10MS):
            trozo = x[i:i + MUESTRAS_10MS]
            if trozo.size < MUESTRAS_10MS:
                trozo = np.pad(trozo, (0, MUESTRAS_10MS - trozo.size))
            frame = rtc.AudioFrame.create(TASA, 1, MUESTRAS_10MS)
            np.frombuffer(frame.data, dtype=np.int16)[:] = (np.clip(trozo, -1, 1) * 32767).astype(np.int16)
            await fuente.capture_frame(frame)
        await fuente.wait_for_playout()
        fin = time.monotonic()
        est["esperando"] = True
        while est["primero_tras"] is None and time.monotonic() - fin < a.tope and not est["colgo"].is_set():
            await asyncio.sleep(0.05)
        est["esperando"] = False
        return (est["primero_tras"] - fin) if est["primero_tras"] else None

    for etiqueta in a.audios.split(","):
        if est["colgo"].is_set():
            break
        x = cargar(resolver(etiqueta))
        lat, intentos = await enviar(x), 1
        # Sin respuesta: la persona lo repite (una vez). Se cuenta aparte: es una respuesta que el agente perdió.
        while lat is None and intentos <= a.reintentos and not est["colgo"].is_set():
            print(f"  {etiqueta:18s} sin respuesta: se vuelve a decir")
            lat, intentos = await enviar(x), intentos + 1
        filas.append({"audio": etiqueta, "seg": round(len(x) / TASA, 1), "latencia_s": round(lat, 2) if lat is not None else None, "intentos": intentos})
        print(f"  {etiqueta:18s} {len(x)/TASA:4.1f}s -> el agente empezó a hablar " + (f"{lat:.2f} s después" if lat is not None else "NUNCA (tope)")
              + (f"  [dicho {intentos} veces]" if intentos > 1 else ""))
        await esperar_silencio_del_agente(1.2, 25)
    await asyncio.sleep(2)
    await sala.disconnect()

    lats = [f["latencia_s"] for f in filas if f["latencia_s"] is not None and f["intentos"] == 1]
    repetidos = sum(1 for f in filas if f["intentos"] > 1)
    if lats:
        print(f"\nlatencia de turno: mediana {np.median(lats):.2f} s | máx {max(lats):.2f} s | n={len(lats)} (a la primera) | audios que hubo que repetir: {repetidos}")
    ruta = f"/data/{sala_nombre}"
    os.makedirs("/data", exist_ok=True)
    if audio_agente:
        with wave.open(ruta + "-agente.wav", "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(TASA)
            w.writeframes(np.concatenate(audio_agente).tobytes())
    if audio_fondo:
        with wave.open(ruta + "-fondo.wav", "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(TASA)
            w.writeframes(np.concatenate(audio_fondo).tobytes())
        fondo = np.concatenate(audio_fondo).astype(np.float32) / 32768.0
        print(f"\npista de fondo: {len(fondo)/TASA:.0f} s, rms {float(np.sqrt(np.mean(fondo**2))):.4f}, pico {float(np.abs(fondo).max()):.2f}")
    json.dump({"sala": sala_nombre, "turnos": filas, "transcripciones": transcripciones}, open(ruta + "-prueba.json", "w"), ensure_ascii=False, indent=1)
    print("\n== lo que se dijo")
    for t in sorted(transcripciones, key=lambda z: z["t"]):
        if t["texto"].strip():
            print(f"  [{t['t']:6.1f}s] {t['quien']:6s}: {t['texto'][:150]}")
    print(f"\nguardado: {ruta}-prueba.json y {ruta}-agente.wav")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--audios", required=True)
    p.add_argument("--url", default=os.getenv("LIVEKIT_URL", "ws://livekit:7880"))
    p.add_argument("--tope", type=float, default=12.0, help="segundos máximos de espera a que el agente conteste")
    p.add_argument("--reintentos", type=int, default=1, help="veces que se repite un audio al que el agente no contestó")
    asyncio.run(main(p.parse_args()))
