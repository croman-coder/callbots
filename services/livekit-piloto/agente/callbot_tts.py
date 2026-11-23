"""La voz de Callbot (Voicebox, perfil de producción) como TTS de LiveKit Agents.

Voicebox devuelve el audio de cada frase entero (POST /generate/stream, un WAV): LiveKit parte el texto del LLM en frases y las pide
de a una, así el agente empieza a hablar cuando está la primera. El audio se entrega a la tasa nativa del WAV (24 kHz), sin bajarlo a 8 kHz.
"""
from __future__ import annotations

import io
import uuid
import wave

import httpx
from livekit.agents import tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

TASA_NATIVA = 24000


class VoiceboxTTS(tts.TTS):
    def __init__(self, *, url: str, perfil: str, motor: str = "kokoro", idioma: str = "es") -> None:
        super().__init__(capabilities=tts.TTSCapabilities(streaming=False), sample_rate=TASA_NATIVA, num_channels=1)
        self._url = url.rstrip("/")
        self._perfil, self._motor, self._idioma = perfil, motor, idioma

    @property
    def model(self) -> str:
        return f"voicebox:{self._motor}"

    @property
    def provider(self) -> str:
        return "callbot"

    def synthesize(self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS) -> tts.ChunkedStream:
        return _Frase(tts=self, input_text=text, conn_options=conn_options)


class _Frase(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        voz: VoiceboxTTS = self._tts  # type: ignore[assignment]
        payload = {"profile_id": voz._perfil, "text": self.input_text, "language": voz._idioma, "engine": voz._motor, "normalize": True}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=self._conn_options.timeout)) as cliente:
            r = await cliente.post(f"{voz._url}/generate/stream", json=payload)
            r.raise_for_status()
        with wave.open(io.BytesIO(r.content), "rb") as w:
            tasa, canales, pcm = w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())
        output_emitter.initialize(request_id=uuid.uuid4().hex, sample_rate=tasa, num_channels=canales, mime_type="audio/pcm")
        output_emitter.push(pcm)
        output_emitter.flush()
