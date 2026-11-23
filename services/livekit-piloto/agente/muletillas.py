"""Sonido de fondo y muletillas del piloto, sobre el `BackgroundAudioPlayer` de LiveKit.

El jugador publica una pista de audio aparte en la sala (`background_audio`): lo que suena ahí se mezcla con la voz del agente en el
oído de la persona y no pasa por el turno ni por las interrupciones del agente.

  * Fondo: una oficina de ambiente en bucle, bajito (`LK_FONDO`, `LK_FONDO_RMS`).
  * «Escucha» (backchannel): mientras la persona habla largo, un «claro» / «entiendo» / «ya» en una pausa corta (ver escucha.py). Un segundo
    VAD, más sensible que el que corta los turnos, mira el micrófono y marca esas pausas.
  * «Pensando»: si el agente tarda en contestar, un «a ver» / «un momento» (`LK_PENSANDO_ESPERA`).

Las palabras se sintetizan una vez con la voz del agente (Voicebox) y quedan en disco. Se usan palabras de verdad y no «mm» o «ajá»: el TTS
actual (Kokoro) no hace bien los sonidos sin letras (Whisper los leyó «Mejor», «Acá es…», «M&M»); con una voz clonada o ElevenLabs sí se podrían.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
from livekit import rtc
from livekit.agents import AudioConfig, BackgroundAudioPlayer, BuiltinAudioClip
from livekit.agents.voice.background_audio import PlayHandle
from livekit.agents.vad import VADEventType

from .clips import acondicionar, escribir_wav, leer_wav
from .escucha import Espaciador, Racha
from .reglas import normalize

log = logging.getLogger("piloto.muletillas")

PALABRAS = {
    "escucha": ["Claro.", "Entiendo.", "Ya."],
    "pensando": ["A ver.", "Un momento.", "Déjeme ver."],
}


def preparar_clips(url: str, perfil: str, carpeta: Path, motor: str = "kokoro") -> dict[str, list[str]]:
    """Sintetiza cada palabra con la voz del agente si todavía no está en `carpeta`. Devuelve las rutas por grupo ({} si Voicebox no contesta)."""
    carpeta.mkdir(parents=True, exist_ok=True)
    salida: dict[str, list[str]] = {}
    for grupo, palabras in PALABRAS.items():
        for palabra in palabras:
            clave = re.sub(r"[^a-z0-9]+", "-", normalize(palabra)).strip("-")
            ruta = carpeta / f"{perfil[:8]}-{grupo}-{clave}.wav"
            if not ruta.exists():
                try:
                    r = httpx.post(f"{url.rstrip('/')}/generate/stream", timeout=60,
                                   json={"profile_id": perfil, "text": palabra, "language": "es", "engine": motor, "normalize": True})
                    r.raise_for_status()
                    x, sr = leer_wav(r.content)
                    x = acondicionar(x, sr)
                    if not len(x):
                        continue
                    escribir_wav(str(ruta), x, sr)
                except Exception:  # noqa: BLE001 - sin muletillas el piloto anda igual
                    log.exception("no se pudo sintetizar la muletilla %r", palabra)
                    continue
            salida.setdefault(grupo, []).append(str(ruta))
    log.info("muletillas listas: %s", {g: len(v) for g, v in salida.items()})
    return salida


def preparar_fondo(carpeta: Path, rms: float) -> str | None:
    """La oficina que trae LiveKit llevada a un nivel que se oiga de fondo.

    El clip de LiveKit (`office-ambience.ogg`) viene casi mudo (rms 0,0017, unos -55 dBFS): con `volume` 0,12 llegaba a 0,0002 y no se oía. Se
    decodifica una vez, se pasa a 24 kHz mono y se lleva a `rms` (por defecto 0,01, unos -40 dBFS: lo que se ve en una oficina por teléfono).
    """
    ruta = carpeta / f"fondo-oficina-{int(rms * 10000)}.wav"
    if ruta.exists():
        return str(ruta)
    try:
        import av  # viene con livekit-agents

        carpeta.mkdir(parents=True, exist_ok=True)
        contenedor = av.open(BuiltinAudioClip.OFFICE_AMBIENCE.path())
        remuestreo = av.AudioResampler(format="s16", layout="mono", rate=24000)
        trozos = []
        for fr in contenedor.decode(contenedor.streams.audio[0]):
            trozos += [o.to_ndarray().ravel() for o in remuestreo.resample(fr)]
        trozos += [o.to_ndarray().ravel() for o in (remuestreo.resample(None) or [])]
        x = np.concatenate(trozos).astype(np.float32) / 32768.0
        actual = float(np.sqrt(np.mean(x**2)))
        if actual <= 0:
            return None
        x = np.clip(x * (rms / actual), -0.9, 0.9)
        escribir_wav(str(ruta), x, 24000)
        log.info("fondo de oficina listo: rms %.4f, %.0f s", rms, len(x) / 24000)
        return str(ruta)
    except Exception:  # noqa: BLE001 - sin fondo la llamada anda igual
        log.exception("no se preparó el fondo")
        return None


class Muletillas:
    def __init__(self, clips: dict[str, list[str]], config: dict, registro: list, inicio: float, llm_listo: Callable[[], bool]) -> None:
        self.clips, self.cfg, self.registro, self._inicio = clips, config, registro, inicio
        self._llm_listo = llm_listo  # True si Claude ya empezó a contestar este turno: la respuesta viene en camino y un «a ver» la pisaría
        self._rellenando: PlayHandle | None = None
        self.player: BackgroundAudioPlayer | None = None
        self._session = None
        self._tareas: list[asyncio.Task] = []
        self._pensando: asyncio.Task | None = None
        self._ult: dict[str, str] = {}
        self._esp_pensando = Espaciador(config["pensando_cada"], maximo=config["pensando_max"])
        self._racha = Racha(config["back_desde"], config["back_cada"])

    # -- arranque y cierre ------------------------------------------------------------------------------------------------
    async def iniciar(self, ctx, session, vad_rapido) -> None:  # noqa: ANN001
        self._session = session
        fondo = AudioConfig(self.clips["fondo"][0], volume=1.0) if self.cfg["fondo"] == "oficina" and self.clips.get("fondo") else None
        self.player = BackgroundAudioPlayer(ambient_sound=fondo)
        await self.player.start(room=ctx.room, agent_session=session)
        if not self.cfg["muletillas"]:
            return
        if self.clips.get("pensando"):
            session.on("agent_state_changed", self._estado_agente)
        if self.clips.get("escucha") and vad_rapido is not None:
            self._tareas.append(asyncio.create_task(self._escuchar(ctx, session, vad_rapido)))

    async def cerrar(self) -> None:
        for t in [*self._tareas, self._pensando]:
            if t and not t.done():
                t.cancel()
        if self.player:
            await self.player.aclose()

    # -- «pensando» -------------------------------------------------------------------------------------------------------
    def _estado_agente(self, ev) -> None:  # noqa: ANN001
        if self._pensando and not self._pensando.done():
            self._pensando.cancel()
        if ev.new_state == "speaking" and self._rellenando and not self._rellenando.done():
            self._rellenando.stop()  # llegó la respuesta: el «a ver» se apaga con un fundido y no se pisan
        if ev.new_state == "thinking":
            self._pensando = asyncio.create_task(self._rellenar())

    async def _rellenar(self) -> None:
        await asyncio.sleep(self.cfg["pensando_espera"])
        # Solo si de verdad se está demorando: con Claude ya contestando la voz sale en medio segundo y la muletilla llegaría encima.
        if self._session.agent_state != "thinking" or self._llm_listo() or not self._esp_pensando.puede(time.monotonic()):
            return
        self._esp_pensando.marcar(time.monotonic())
        self._rellenando = self._decir("pensando")

    # -- «escucha» --------------------------------------------------------------------------------------------------------
    async def _escuchar(self, ctx, session, vad) -> None:  # noqa: ANN001
        try:
            participante = await ctx.wait_for_participant()
            audio = rtc.AudioStream.from_participant(participant=participante, track_source=rtc.TrackSource.SOURCE_MICROPHONE,
                                                     sample_rate=16000, num_channels=1)
            flujo = vad.stream()

            async def empujar() -> None:
                async for ev in audio:
                    flujo.push_frame(ev.frame)
                flujo.end_input()

            self._tareas.append(asyncio.create_task(empujar()))
            async for ev in flujo:
                ahora = time.monotonic()
                if ev.type == VADEventType.START_OF_SPEECH:
                    self._racha.inicio_de_voz(ahora)
                elif ev.type == VADEventType.END_OF_SPEECH and self._racha.fin_de_voz(ahora, session.agent_state == "listening"):
                    self._decir("escucha")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - las muletillas son un extra: si falla, la llamada sigue
            log.exception("falló la escucha de las muletillas")

    # -- reproducir -------------------------------------------------------------------------------------------------------
    def _decir(self, grupo: str) -> PlayHandle | None:
        opciones = [c for c in self.clips.get(grupo, []) if c != self._ult.get(grupo)] or self.clips.get(grupo, [])
        if not opciones or not self.player:
            return None
        clip = random.choice(opciones)
        self._ult[grupo] = clip
        manejo = self.player.play(AudioConfig(clip, volume=self.cfg["muletillas_vol"], fade_out=0.12))
        self.registro.append({"t": round(time.time() - self._inicio, 2), "tipo": grupo, "clip": Path(clip).stem})
        log.info("muletilla %s: %s", grupo, Path(clip).stem)
        return manejo


def configuracion() -> dict:
    """Los botones, por variable de entorno (README del piloto)."""
    return {
        "fondo": os.getenv("LK_FONDO", "oficina"),  # «oficina» o «ninguno»
        "fondo_rms": float(os.getenv("LK_FONDO_RMS", "0.01")),  # nivel del fondo (rms, 0,01 = unos -40 dBFS)
        "muletillas": os.getenv("LK_MULETILLAS", "1") != "0",
        "muletillas_vol": float(os.getenv("LK_MULETILLAS_VOL", "0.8")),
        "back_desde": float(os.getenv("LK_BACK_DESDE", "3.0")),  # la persona lleva hablando tantos segundos de corrido
        "back_cada": float(os.getenv("LK_BACK_CADA", "7.0")),
        "pensando_espera": float(os.getenv("LK_PENSANDO_ESPERA", "1.0")),  # el agente lleva tantos segundos sin que Claude conteste
        "pensando_cada": float(os.getenv("LK_PENSANDO_CADA", "10.0")),
        "pensando_max": int(os.getenv("LK_PENSANDO_MAX", "3")),
    }
