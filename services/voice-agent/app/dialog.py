"""Máquina de estados de la encuesta.

Modo guiado: el bot lee cada pregunta, escucha la respuesta, la transcribe y
avanza. No hay LLM en el camino crítico, así que la latencia entre que el
cliente termina de hablar y arranca la siguiente pregunta es la del ASR.

Quién decide qué: el agente maneja audio y tiempos; la API decide si una
respuesta se entendió y si hay que repreguntar. Toda la lógica de negocio vive
en un solo lado.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid as uuid_mod
import wave
from datetime import datetime, timezone

from app import buzon, stt, tts
from app.api_client import (
    FRASE_LLAMAR_DESPUES,
    FRASE_TRANSFERENCIA_FALLIDA,
    MULETILLA,
    ApiClient,
    ApiError,
    Question,
    Script,
)
from app.audiosocket import AudioSocket, AudioSocketClosed
from app.config import SAMPLE_RATE, SAMPLE_WIDTH, config
from app.guion import partir_frases, personalizar
from app.listener import ListenResult, listen

log = logging.getLogger(__name__)

# Pausa después de una pregunta, antes de empezar a escuchar. Sin esto el VAD
# capta el final de la propia voz del bot como si fuera el cliente.
PAUSE_AFTER_PROMPT_MS = 300
# Un respiro entre la respuesta del cliente y la pregunta que sigue: sin esto el bot arranca
# la próxima pregunta pisando el final de la respuesta y todo suena atropellado.
PAUSE_BEFORE_QUESTION_MS = 450


class Outcome:
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    VOICEMAIL = "voicemail"
    TRANSFERRED = "transferred"


_personalize = personalizar


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
        profile_id=script.voice_profile_id,
    )


# Recepción: el cliente llama y cuenta su problema de corrido, así que cada turno escucha más que una respuesta de
# encuesta. Con tope de duración: una llamada que no termina ocupa la línea.
RECEPCION_MAX_SEGUNDOS = 8 * 60
RECEPCION_TURNO_SEGUNDOS = 40
RECEPCION_ESPERA_VOZ = 6
# Si Claude tarda más que esto en contestar, se dice la muletilla («Déjeme ver.») para no dejar un silencio. Una
# respuesta rápida sale sin muletilla: decirla en cada turno suena a robot.
ESPERA_ANTES_DE_MULETILLA = 0.9


async def _say(
    socket: AudioSocket, text: str, params: tts.VoiceParams = tts.DEFAULT_PARAMS
) -> None:
    """Dice el texto frase por frase: mientras suena una, ya se está generando la siguiente, así la
    voz buena no agrega espera y entre frase y frase queda una pausa natural."""
    await socket.send_event("agente", text)
    frases = partir_frases(text)
    if not frases:
        return
    siguiente = asyncio.create_task(tts.sintetizar_frase(frases[0], params))
    try:
        for i in range(len(frases)):
            pcm = await siguiente
            if i + 1 < len(frases):
                siguiente = asyncio.create_task(tts.sintetizar_frase(frases[i + 1], params))
            if pcm:
                await socket.play(pcm)
            if i + 1 < len(frases):
                await socket.play_silence(tts.PAUSA_ENTRE_FRASES_MS)
    finally:
        if not siguiente.done():
            siguiente.cancel()


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
        if self.script.es_recepcion:
            log.info("[%s] Recepción '%s'%s", self.session_uuid, self.script.campaign_name,
                     " (DEMO)" if self.script.demo else "")
        else:
            log.info(
                "[%s] Encuesta '%s': %d preguntas%s",
                self.session_uuid,
                self.script.campaign_name,
                len(self.script.questions),
                " (DEMO)" if self.script.demo else "",
            )

        try:
            if self.script.es_recepcion:
                # Llamó el cliente: no hay contestador que detectar ni encuesta que reportar; todo queda en el caso.
                await self._atender_recepcion()
                return
            if self._detecta_buzon and await self._es_buzon():
                await self._atender_buzon()
            else:
                await self._conducir_encuesta()

        except AudioSocketClosed as exc:
            self.hangup_cause = self.hangup_cause or str(exc)
            log.info("[%s] El cliente cortó: %s", self.session_uuid, exc)
            if self.script.es_recepcion:
                return

        await self._report()

    @property
    def _detecta_buzon(self) -> bool:
        # En una prueba desde el navegador o el softphone no hay contestador: quien prueba habla de
        # corrido y se lo tomaría por una máquina.
        return bool(self.script.voicemail_action) and not self.script.demo

    async def _conducir_encuesta(self) -> None:
        if not await self._greet():
            return

        for question in self.script.questions:
            keep_going = await self._ask(question)
            if not keep_going:
                break
        else:
            # Recorrió todas las preguntas sin cortes
            self.outcome = Outcome.COMPLETED
            await _say(self.socket, self.script.outro_script, self._voz)

    # ------------------------------------------------------------------
    # Recepción: el cliente llamó
    # ------------------------------------------------------------------
    async def _atender_recepcion(self) -> None:
        """Saluda, escucha y conversa turno a turno: la API (Claude) decide qué decir y si deriva o termina.

        El caso se abre antes de saludar y se cierra siempre, también si el cliente corta a la mitad: lo que llegó a
        contar queda derivado para que alguien lo llame.
        """
        caso = await self.api.recepcion_inicio(self.session_uuid)
        frases = self.script.frases
        inicio = asyncio.get_running_loop().time()
        try:
            await _say(self.socket, self.script.intro_script, self._voz)
            silencios = 0
            while True:
                if asyncio.get_running_loop().time() - inicio > RECEPCION_MAX_SEGUNDOS:
                    self.hangup_cause = "tiempo_maximo"
                    await _say(self.socket, frases.get("tiempo", ""), self._voz)
                    return
                await self.socket.play_silence(PAUSE_AFTER_PROMPT_MS)
                grabacion = await listen(
                    self.socket, max_seconds=RECEPCION_TURNO_SEGUNDOS, no_speech_timeout=RECEPCION_ESPERA_VOZ
                )
                if grabacion.result is ListenResult.HANGUP:
                    raise AudioSocketClosed("cortó")
                texto = ""
                if grabacion.has_audio:
                    texto = (await stt.transcribe(grabacion.pcm, self.script.vocabulario)).text.strip()
                if not texto:
                    silencios += 1
                    if silencios >= 2:
                        self.hangup_cause = "sin_respuesta"
                        await _say(self.socket, frases.get("sin_respuesta", ""), self._voz)
                        return
                    await _say(self.socket, frases.get("sigue_ahi", ""), self._voz)
                    continue

                silencios = 0
                await self.socket.send_event("cliente", texto)
                log.info("[%s] Recepción <- %r", self.session_uuid, texto[:80])
                respuesta = await self._esperar_con_muletilla(self.api.recepcion_turno(caso, texto))
                if respuesta.get("accion") == "consultar":
                    # «Ya lo busco en el sistema»: la consulta arranca antes de decirlo, así la frase tapa la espera.
                    pedido = asyncio.ensure_future(self.api.recepcion_continuar(caso))
                    try:
                        await _say(self.socket, respuesta.get("decir") or "", self._voz)
                        respuesta = await pedido
                    except BaseException:
                        pedido.cancel()
                        raise
                accion = respuesta.get("accion")
                await _say(self.socket, respuesta.get("decir") or frases.get("registrado", ""), self._voz)

                if accion == "transferir" and self.script.transfer_enabled:
                    if await self._transferir():
                        return
                    self.hangup_cause = "transferencia_fallida"
                    await _say(self.socket, frases.get("registrado", ""), self._voz)
                    return
                if accion == "terminar":
                    self.outcome = Outcome.COMPLETED
                    self.hangup_cause = self.hangup_cause or "se_despidio"
                    return
        except AudioSocketClosed as exc:
            self.hangup_cause = self.hangup_cause or f"cortó el cliente ({exc})"
            raise
        finally:
            await asyncio.shield(self.api.recepcion_fin(caso, self.hangup_cause))

    async def _esperar_con_muletilla(self, pedido_api) -> dict:  # noqa: ANN001 - corrutina del ApiClient
        """Espera la respuesta de la API; si tarda, dice la muletilla mientras tanto (no después)."""
        pedido = asyncio.ensure_future(pedido_api)
        try:
            listos, _ = await asyncio.wait({pedido}, timeout=ESPERA_ANTES_DE_MULETILLA)
            if not listos:
                await _say(self.socket, MULETILLA, self._voz)
            return await pedido
        except BaseException:
            pedido.cancel()
            raise

    # ------------------------------------------------------------------
    # Buzón de voz
    # ------------------------------------------------------------------
    async def _es_buzon(self) -> bool:
        """Escucha los primeros segundos, antes de saludar, y decide si es un contestador."""
        grabacion = await listen(
            self.socket,
            max_seconds=buzon.SONDEO_SEGUNDOS,
            silence_ms=buzon.SILENCIO_MS,
            no_speech_timeout=buzon.ESPERA_VOZ_SEGUNDOS,
        )
        if grabacion.result is ListenResult.HANGUP:
            raise AudioSocketClosed("cortó antes de empezar")

        texto = ""
        if grabacion.has_audio and grabacion.speech_ms >= buzon.MIN_VOZ_MS:
            texto = (await stt.transcribe(grabacion.pcm)).text
        es = buzon.es_buzon(grabacion.result.value, texto)
        log.info(
            "[%s] Sondeo de buzón: %s %dms %r -> %s",
            self.session_uuid, grabacion.result.value, grabacion.speech_ms, texto[:80],
            "BUZÓN" if es else "persona",
        )
        return es

    async def _atender_buzon(self) -> None:
        """Cuelga o deja el mensaje. Nunca sigue con la encuesta: no hay nadie para contestarla."""
        self.outcome = Outcome.VOICEMAIL
        if self.script.voicemail_action != "mensaje" or not self.script.voicemail_script.strip():
            self.hangup_cause = "buzon_colgo"
            log.info("[%s] Buzón de voz: se corta", self.session_uuid)
            return

        await self._esperar_el_tono()
        await _say(
            self.socket, _personalize(self.script.voicemail_script, self.script.contact_name), self._voz
        )
        await self.socket.play_silence(500)
        self.hangup_cause = "buzon_mensaje"
        log.info("[%s] Buzón de voz: mensaje dejado", self.session_uuid)

    async def _esperar_el_tono(self) -> None:
        """Deja pasar el saludo del contestador: el mensaje se graba recién después del tono.

        Se escucha hasta que haya una pausa larga, que es donde suena el tono, y se espera un poco más.
        Con un tope: un contestador que no calla no puede tener la línea abierta para siempre.
        """
        transcurrido = float(buzon.SONDEO_SEGUNDOS)
        while transcurrido < buzon.ESPERA_MAXIMA_SEGUNDOS:
            grabacion = await listen(
                self.socket,
                max_seconds=10,
                silence_ms=buzon.SILENCIO_FIN_SALUDO_MS,
                no_speech_timeout=3,
            )
            if grabacion.result is ListenResult.HANGUP:
                raise AudioSocketClosed("cortó el contestador")
            if grabacion.result is ListenResult.SILENCE:
                transcurrido += 3
                break  # ya pasaron 3 s de silencio: el saludo terminó, el tono es ahora
            transcurrido += grabacion.duration_seconds + buzon.SILENCIO_FIN_SALUDO_MS / 1000
            if grabacion.result is ListenResult.SPEECH:
                break  # habló y terminó con una pausa larga: el saludo terminó
        await self.socket.play_silence(buzon.ESPERA_TONO_MS)

    # ------------------------------------------------------------------
    async def _greet(self) -> bool:
        """Saluda y escucha la respuesta al «¿tiene un minutito?». Devuelve False si la llamada termina acá.

        Lo que conteste se transcribe y la API decide (reglas primero; Claude solo si preguntó algo): «sí, dale» o
        nada → sigue; «ahora no puedo» → corta amable y se lo llama en otro momento; «no me llamen» → frase de baja;
        pide un asesor → se transfiere; pregunta → se le contesta y se vuelve a escuchar, una sola vez.
        """
        intro = _personalize(self.script.intro_script, self.script.contact_name)
        await _say(self.socket, intro, self._voz)

        for intento in range(2):
            recording = await listen(self.socket, max_seconds=6, silence_ms=800, no_speech_timeout=3)
            if recording.result is ListenResult.HANGUP:
                raise AudioSocketClosed("cortó durante la presentación")
            if not recording.has_audio:
                return True  # no dijo nada: se arranca

            transcript = (await stt.transcribe(recording.pcm, self.script.vocabulario)).text.strip()
            await self.socket.send_event("cliente", transcript)
            log.info("[%s] Saludo -> %r", self.session_uuid, transcript)
            if not transcript:
                return True

            pedido = asyncio.create_task(self.api.greeting(self.session_uuid, transcript))
            if "?" in transcript and intento == 0:
                # Si preguntó algo, contesta Claude: la muletilla tapa la espera en vez de sumarse a ella.
                await _say(self.socket, MULETILLA, self._voz)
            decision = await pedido
            accion = decision.get("accion")

            if accion == "reprogramar":
                self.hangup_cause = "pidio_llamar_despues"
                await _say(self.socket, FRASE_LLAMAR_DESPUES, self._voz)
                return False
            if accion == "baja":
                self.hangup_cause = "pidio_no_llamar"
                await _say(self.socket, self.script.optout_script, self._voz)
                return False
            if accion == "asesor" and self.script.transfer_enabled:
                if await self._transferir():
                    return False
                self.hangup_cause = "transferencia_fallida"
                await _say(self.socket, FRASE_TRANSFERENCIA_FALLIDA, self._voz)
                return True
            if accion == "responder" and decision.get("respuesta") and intento == 0:
                await _say(self.socket, decision["respuesta"], self._voz)
                continue  # se escucha de nuevo: la respuesta vuelve a pedir el minuto
            return True
        return True

    # ------------------------------------------------------------------
    async def _ask(self, question: Question) -> bool:
        """Hace una pregunta. Devuelve False si hay que terminar la llamada."""
        retries_used = 0
        # Lo que el LLM contestó al intento anterior. Se dice antes de volver
        # a preguntar, en lugar de la frase fija.
        respuesta_libre: str | None = None

        if self.questions_asked > 0:
            await self.socket.play_silence(PAUSE_BEFORE_QUESTION_MS)

        while True:
            if retries_used == 0:
                prompt = question.text
            elif respuesta_libre:
                # El cliente preguntó algo o se fue por las ramas: se le
                # contesta. El LLM ya termina reconduciendo a la pregunta, así
                # que volver a leerla suena a que el bot se trabó. Si por lo
                # que sea no la incluyó, se la repite.
                await _say(self.socket, respuesta_libre, self._voz)
                prompt = None if "?" in respuesta_libre else question.text
            else:
                prompt = self.script.fallback_script

            if prompt:
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
                result = await stt.transcribe(recording.pcm, self.script.vocabulario)
                transcript = result.text
                confidence = result.confidence

            await self.socket.send_event("cliente", transcript)
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

            if verdict.wants_transfer and self.script.transfer_enabled:
                if await self._transferir():
                    return False
                # No se pudo: se le explica y se sigue con la encuesta, que es lo menos malo.
                self.hangup_cause = "transferencia_fallida"
                await _say(self.socket, FRASE_TRANSFERENCIA_FALLIDA, self._voz)
                return True

            if verdict.should_retry and not transcript.strip():
                # No dijo nada (o no se entendió ni una palabra): no hay qué contestarle. Se repite con la frase
                # fija, sin «déjeme ver» —antes de «no lo escuché» suena a que el bot pensó en el silencio— y sin
                # esperar a Claude.
                respuesta_libre = None
                retries_used += 1
                continue

            if verdict.should_retry:
                # Solo acá se llama al LLM: si la respuesta se entendió, la
                # llamada sigue sin pagar un segundo de latencia extra.
                #
                # La consulta arranca ANTES de la muletilla y se espera
                # después: el "déjeme ver" tapa la latencia en vez de sumarse
                # a ella. Sin esto son hasta 3,5 segundos de silencio con
                # alguien del otro lado preguntándose si se cortó.
                pedido = asyncio.create_task(
                    self.api.conversational_reply(
                        session_uuid=self.session_uuid,
                        question_text=question.text,
                        transcript=transcript,
                        retries_used=retries_used,
                    )
                )
                await _say(self.socket, MULETILLA, self._voz)
                respuesta_libre = await pedido
                if respuesta_libre:
                    log.info(
                        "[%s] P%d respuesta libre: %r",
                        self.session_uuid, question.position, respuesta_libre[:70],
                    )
                retries_used += 1
                continue

            if verdict.understood:
                self.questions_answered += 1

            return True

    # ------------------------------------------------------------------
    async def _transferir(self) -> bool:
        """Le avisa al cliente, le pide a la API que prepare el canal y deja que el audio termine:
        al cerrarse el AudioSocket el dialplan sigue y marca al asesor (ver extensions.conf)."""
        log.info("[%s] El cliente pidió un asesor", self.session_uuid)
        await _say(self.socket, self.script.transfer_script or "Un momento, lo comunico.", self._voz)

        if self.script.demo:
            # Probando desde el navegador o el softphone no hay a quién marcar.
            await self.socket.send_event("agente", "(En una llamada real, acá se le pasa la llamada al asesor)")
            return True

        if not await self.api.transfer(self.session_uuid):
            return False
        self.outcome = Outcome.TRANSFERRED
        self.hangup_cause = "transferida"
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
