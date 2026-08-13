"""Cliente de la API del callbot.

El voice-agent no toca la base de datos ni Bitrix: solo consume estos endpoints.
Así la lógica de negocio queda en un solo lugar y este servicio se limita a
manejar audio.
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import config

log = logging.getLogger(__name__)


class ApiError(RuntimeError):
    pass


@dataclass
class Question:
    id: int
    position: int
    text: str
    qtype: str
    is_required: bool
    max_answer_seconds: int
    max_retries: int


@dataclass
class Script:
    session_uuid: str
    call_id: int | None
    demo: bool
    campaign_id: int
    campaign_name: str
    contact_name: str | None
    intro_script: str
    outro_script: str
    fallback_script: str
    optout_script: str
    questions: list[Question]

    @property
    def all_prompts(self) -> list[str]:
        """Todo el texto fijo, para precalentar el caché del TTS."""
        return [
            self.intro_script,
            self.outro_script,
            self.fallback_script,
            self.optout_script,
            *[q.text for q in self.questions],
        ]


@dataclass
class AnswerVerdict:
    understood: bool
    value: float | None
    is_optout: bool
    is_silence: bool
    should_retry: bool


class ApiClient:
    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=config.api_base_url.rstrip("/"),
            headers={"X-Callbot-Token": config.internal_token},
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"POST {path} -> HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"POST {path} falló: {exc}") from exc

    # ------------------------------------------------------------------
    async def get_script(self, session_uuid: uuid_mod.UUID) -> Script:
        path = f"/internal/sessions/{session_uuid}/script"
        try:
            response = await self._client.get(path)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiError(
                f"GET {path} -> HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"GET {path} falló: {exc}") from exc

        data = response.json()
        return Script(
            session_uuid=data["session_uuid"],
            call_id=data.get("call_id"),
            demo=data.get("demo", False),
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            contact_name=data.get("contact_name"),
            intro_script=data["intro_script"],
            outro_script=data["outro_script"],
            fallback_script=data["fallback_script"],
            optout_script=data["optout_script"],
            questions=[Question(**q) for q in data["questions"]],
        )

    async def get_all_prompts(self) -> list[str]:
        """Texto fijo de todas las campañas activas, para precalentar el TTS."""
        try:
            response = await self._client.get("/internal/prompts")
            response.raise_for_status()
            return response.json().get("texts", [])
        except httpx.HTTPError as exc:
            log.warning("No se pudieron traer los textos a precalentar: %s", exc)
            return []

    async def session_started(self, session_uuid: uuid_mod.UUID) -> None:
        await self._post(f"/internal/sessions/{session_uuid}/started", {})

    async def submit_answer(
        self,
        session_uuid: uuid_mod.UUID,
        question_id: int,
        transcript: str,
        asr_confidence: float | None,
        duration_seconds: float,
        retries_used: int,
        audio_path: str | None = None,
    ) -> AnswerVerdict:
        data = await self._post(
            f"/internal/sessions/{session_uuid}/answers",
            {
                "question_id": question_id,
                "transcript": transcript,
                "asr_confidence": asr_confidence,
                "duration_seconds": duration_seconds,
                "retries_used": retries_used,
                "audio_path": audio_path,
            },
        )
        return AnswerVerdict(
            understood=data["understood"],
            value=data.get("value"),
            is_optout=data.get("is_optout", False),
            is_silence=data.get("is_silence", False),
            should_retry=data.get("should_retry", False),
        )

    async def session_finished(
        self,
        session_uuid: uuid_mod.UUID,
        outcome: str,
        questions_asked: int,
        questions_answered: int,
        hangup_cause: str | None = None,
        recording_path: str | None = None,
    ) -> None:
        await self._post(
            f"/internal/sessions/{session_uuid}/finished",
            {
                "outcome": outcome,
                "questions_asked": questions_asked,
                "questions_answered": questions_answered,
                "hangup_cause": hangup_cause,
                "recording_path": recording_path,
            },
        )
