"""Máquina de estados de la encuesta.

Modo guiado: el bot lee cada pregunta, escucha la respuesta, la transcribe y
avanza. No hay LLM en el camino crítico, así que la latencia entre que el
cliente termina de hablar y arranca la siguiente pregunta es la del ASR.

Quién decide qué: el agente maneja audio y tiempos; la API decide si una
respuesta se entendió y si hay que repreguntar. Toda la lógica de negocio vive
en un solo lado.
"""

from __future__ import annotations

import logging
import os
import uuid as uuid_mod
import wave
from datetime import datetime, timezone

from app import stt, tts
from app.api_client import ApiClient, ApiError, Question, Script
from app.audiosocket import AudioSocket, AudioSocketClosed
from app.config import SAMPLE_RATE, SAMPLE_WIDTH, config
from app.listener import ListenResult, listen

log = logging.getLogger(__name__)

# Pausa después de una pregunta, antes de empezar a escuchar. Sin esto el VAD
# capta el final de la propia voz del bot como si fuera el cliente.
PAUSE_AFTER_PROMPT_MS = 300


class Outcome:
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


def _personalize(text: str, contact_name: str | None) -> str:
    """Reemplaza {nombre} en el guion. Sin nombre, deja la frase natural."""
    if "{nombre}" not in text:
        return text
    if contact_name:
        return text.replace("{nombre}", contact_name)
    # Sin nombre: saca el placeholder y limpia el espacio doble que queda
    return text.replace("{nombre}", "").replace("  ", " ").replace(" ,", ",")


def _save_wav(pcm: bytes, session_uuid: uuid_mod.UUID, position: int) -> str | None:
    """Guarda el audio de una respuesta. Sirve para auditar transcripciones dudosas."""
    if not config.save_audio or not pcm:
        return None

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    directory = os.path.join(config.recordings_dir, day)
    path = os.path.join(directory, f"{session_uuid}_p{position:02d}.wav")

    try:
        os.makedirs(directory, exist_ok=True)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(SAMPLE_WIDTH)
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(pcm)
        return path
    except OSError as exc:
        log.warning("No se pudo guardar %s: %s", path, exc)
        return None


def voz_de(script: Script) -> tts.VoiceParams:
    """Modulación de la voz que pidió la campaña."""
    return tts.VoiceParams(
        speed=script.voice_speed,
        pitch=script.voice_pitch,
        expressiveness=script.voice_expressiveness,
        volume=script.voice_volume,
    )


async def _say(
    socket: AudioSocket, text: str, params: tts.VoiceParams = tts.DEFAULT_PARAMS
) -> None:
    pcm = await tts.synthesize(text, params=params)
    if pcm:
        await socket.play(pcm)


class SurveyDialog:
    """Conduce una encuesta sobre un canal ya conectado."""

    def __init__(
        self, socket: AudioSocket, api: ApiClient, script: Script
    ) -> None:
        self.socket = socket
        self.api = api
        self.script = script
        self.session_uuid = socket.session_uuid
        self._voz = voz_de(script)
        self.questions_asked = 0
        self.questions_answered = 0
        self.outcome = Outcome.PARTIAL
        self.hangup_cause: str | None = None

    # ------------------------------------------------------------------
    async def run(self) -> None:
        log.info(
            "[%s] Encuesta '%s': %d preguntas%s",
            self.session_uuid,
            self.script.campaign_name,
            len(self.script.questions),
            " (DEMO)" if self.script.demo else "",
        )

        try:
            await self._greet()

            for question in self.script.questions:
                keep_going = await self._ask(question)
                if not keep_going:
                    break
            else:
                # Recorrió todas las preguntas sin cortes
                self.outcome = Outcome.COMPLETED
                await _say(self.socket, self.script.outro_script, self._voz)

        except AudioSocketClosed as exc:
            self.hangup_cause = str(exc)
            log.info("[%s] El cliente cortó: %s", self.session_uuid, exc)

        await self._report()

    # ------------------------------------------------------------------
    async def _greet(self) -> None:
        intro = _personalize(self.script.intro_script, self.script.contact_name)
        await _say(self.socket, intro, self._voz)

        # Damos lugar a un "sí, dígame" antes de arrancar con la primera pregunta.
        # No se transcribe: solo se espera a que termine de hablar.
        recording = await listen(
            self.socket, max_seconds=6, silence_ms=800, no_speech_timeout=3
        )
        if recording.result is ListenResult.HANGUP:
            raise AudioSocketClosed("cortó durante la presentación")

    # ------------------------------------------------------------------
    async def _ask(self, question: Question) -> bool:
        """Hace una pregunta. Devuelve False si hay que terminar la llamada."""
        retries_used = 0

        while True:
            prompt = question.text if retries_used == 0 else self.script.fallback_script
            await _say(self.socket, prompt, self._voz)
            await self.socket.play_silence(PAUSE_AFTER_PROMPT_MS)

            if retries_used == 0:
                self.questions_asked += 1

            recording = await listen(
                self.socket,
                max_seconds=question.max_answer_seconds,
                no_speech_timeout=config.no_speech_timeout_seconds,
            )

            if recording.result is ListenResult.HANGUP:
                raise AudioSocketClosed("cortó durante una respuesta")

            transcript = ""
            confidence = None

            if recording.has_audio:
                result = await stt.transcribe(recording.pcm)
                transcript = result.text
                confidence = result.confidence

            log.info(
                "[%s] P%d %r -> %r (%s, %.1fs)",
                self.session_uuid, question.position, question.text[:40],
                transcript, recording.result.value, recording.duration_seconds,
            )

            audio_path = _save_wav(recording.pcm, self.session_uuid, question.position)

            try:
                verdict = await self.api.submit_answer(
                    session_uuid=self.session_uuid,
                    question_id=question.id,
                    transcript=transcript,
                    asr_confidence=confidence,
                    duration_seconds=recording.duration_seconds,
                    retries_used=retries_used,
                    audio_path=audio_path,
                )
            except ApiError as exc:
                # No podemos guardar la respuesta, pero cortar la llamada es peor:
                # seguimos preguntando y el watchdog levanta lo que quedó.
                log.error("[%s] No se pudo guardar la respuesta: %s", self.session_uuid, exc)
                return True

            if verdict.is_optout:
                log.info("[%s] El cliente pidió no continuar", self.session_uuid)
                await _say(self.socket, self.script.optout_script, self._voz)
                self.outcome = Outcome.PARTIAL
                return False

            if verdict.should_retry:
                retries_used += 1
                continue

            if verdict.understood:
                self.questions_answered += 1

            return True

    # ------------------------------------------------------------------
    async def _report(self) -> None:
        if self.script.demo:
            log.info("[%s] Demo terminada, no se reporta nada", self.session_uuid)
            return

        try:
            await self.api.session_finished(
                session_uuid=self.session_uuid,
                outcome=self.outcome,
                questions_asked=self.questions_asked,
                questions_answered=self.questions_answered,
                hangup_cause=self.hangup_cause,
            )
            log.info(
                "[%s] Reportada como %s (%d/%d respondidas)",
                self.session_uuid, self.outcome,
                self.questions_answered, self.questions_asked,
            )
        except ApiError as exc:
            # El watchdog de la API la va a recuperar por timeout
            log.error("[%s] No se pudo cerrar la sesión: %s", self.session_uuid, exc)
