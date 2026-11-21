"""One Gemini call per ticket, returning a validated Decision.

Model calls have no side effects, so every failure (overload, rate limit, timeout) is safe to retry.
If the model stays unavailable or returns unusable output, an exception is raised and the caller hands
the ticket to a human instead of guessing.
"""

import logging
import time
from dataclasses import dataclass

import httpx
from google import genai
from google.genai import errors, types

from agent.config import Settings
from agent.prompt import system_instruction, ticket_message
from agent.schema import Decision

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 2.0
MAX_WAIT_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 120
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ModelUnavailable(Exception):
    """The model could not be reached after all retries."""


class InvalidModelOutput(Exception):
    """The model answered, but not with a usable decision."""


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    seconds: float


class DecisionModel:
    def __init__(self, settings: Settings, thinking_level: str = "low", sleep=time.sleep):
        self._client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_SECONDS * 1000),
        )
        self._model = settings.model
        self._sleep = sleep
        self._config = types.GenerateContentConfig(
            system_instruction=system_instruction(),
            response_mime_type="application/json",
            response_schema=Decision,
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level.upper()),
        )

    def decide(self, ticket: dict) -> tuple[Decision, Usage]:
        message = ticket_message(ticket)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=message, config=self._config
                )
            except errors.APIError as exc:
                if exc.code not in RETRYABLE_STATUS:
                    raise
                self._wait_before_retry(attempt, f"{exc.code} {exc.status}", self._suggested_delay(exc))
                continue
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self._wait_before_retry(attempt, type(exc).__name__)
                continue

            return self._parse(response), self._usage(response, time.monotonic() - started)

        raise AssertionError("unreachable: the loop either returns or raises")

    def _wait_before_retry(self, attempt: int, reason: str, suggested: float | None = None) -> None:
        if attempt == MAX_ATTEMPTS:
            raise ModelUnavailable(f"{self._model}: {reason} after {attempt} attempts")
        backoff = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
        wait = min(max(backoff, suggested or 0.0), MAX_WAIT_SECONDS)
        logger.warning("Model %s: %s, retry %d in %.0fs", self._model, reason, attempt, wait)
        self._sleep(wait)

    @staticmethod
    def _suggested_delay(exc: errors.APIError) -> float | None:
        """Gemini rate-limit errors include a RetryInfo entry such as {"retryDelay": "34s"}."""
        details = (exc.details or {}).get("error", {}).get("details", []) if isinstance(exc.details, dict) else []
        for item in details:
            delay = str(item.get("retryDelay", ""))
            if delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    return None
        return None

    @staticmethod
    def _parse(response) -> Decision:
        if isinstance(response.parsed, Decision):
            return response.parsed
        finish = response.candidates[0].finish_reason if response.candidates else "no candidates"
        raise InvalidModelOutput(f"no valid decision in model output (finish reason: {finish})")

    @staticmethod
    def _usage(response, seconds: float) -> Usage:
        meta = response.usage_metadata
        return Usage(
            input_tokens=(meta.prompt_token_count or 0) if meta else 0,
            output_tokens=(meta.candidates_token_count or 0) if meta else 0,
            thinking_tokens=(meta.thoughts_token_count or 0) if meta else 0,
            seconds=round(seconds, 1),
        )
