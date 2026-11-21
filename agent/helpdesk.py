"""Client for the Roosterly helpdesk API.

All requests go through HelpdeskClient._request, which applies the reliability rules in one place:

- Rate limit: at most MAX_REQUESTS_PER_SECOND, so the helpdesk limit of 5 per second is never reached.
- 503 service_unavailable: nothing was saved, so the request is retried with exponential backoff.
- 429 rate_limited: wait for the Retry-After header, then retry.
- Timeout or connection error: a GET is retried. For a POST the outcome is unknown (the helpdesk may have
  saved the decision before the connection dropped), so HelpdeskOutcomeUnknown is raised and the caller
  must check the ticket before trying again. Retrying blindly could answer a customer twice.
- Any other error (400, 401, 404, 409, 422) is raised immediately; retrying would not change the result.
"""

import logging
import time
from collections.abc import Iterator

import httpx

from agent.config import Settings

logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_SECOND = 4
MAX_ATTEMPTS = 6
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_MAX_SECONDS = 8.0
DEFAULT_RETRY_AFTER_SECONDS = 1.0


class HelpdeskError(Exception):
    """An error response from the helpdesk, with its HTTP status and error code."""

    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(f"{status_code} {error}: {message}")
        self.status_code = status_code
        self.error = error
        self.message = message


class HelpdeskOutcomeUnknown(Exception):
    """A write request timed out or lost its connection; it may or may not have been saved."""


class HelpdeskClient:
    def __init__(
        self,
        settings: Settings,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep=time.sleep,
    ):
        headers = {
            "Authorization": f"Bearer {settings.helpdesk_api_key}",
            "X-Candidate-Email": settings.candidate_email,
        }
        if settings.candidate_name:
            headers["X-Candidate-Name"] = settings.candidate_name
        self._http = httpx.Client(
            base_url=settings.helpdesk_base_url, headers=headers, timeout=timeout, transport=transport
        )
        self._sleep = sleep
        self._min_interval = 1.0 / MAX_REQUESTS_PER_SECOND
        self._last_request_at = 0.0

    def __enter__(self) -> "HelpdeskClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            return max(float(response.headers.get("Retry-After", "")), 0.0)
        except ValueError:
            return DEFAULT_RETRY_AFTER_SECONDS

    def _request(self, method: str, path: str, **kwargs) -> dict:
        is_write = method.upper() != "GET"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                response = self._http.request(method, path, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if is_write:
                    raise HelpdeskOutcomeUnknown(f"{method} {path}: {type(exc).__name__}") from exc
                if attempt == MAX_ATTEMPTS:
                    raise
                wait = self._backoff(attempt)
                logger.warning("%s %s: %s, retry %d in %.1fs", method, path, type(exc).__name__, attempt, wait)
                self._sleep(wait)
                continue

            if response.is_success:
                return response.json()

            if response.status_code == 503 and attempt < MAX_ATTEMPTS:
                wait = self._backoff(attempt)
                logger.warning("%s %s: 503, retry %d in %.1fs", method, path, attempt, wait)
                self._sleep(wait)
                continue

            if response.status_code == 429 and attempt < MAX_ATTEMPTS:
                wait = self._retry_after(response)
                logger.warning("%s %s: 429, retry %d in %.1fs", method, path, attempt, wait)
                self._sleep(wait)
                continue

            raise self._error_from(response)

        raise AssertionError("unreachable: the loop either returns or raises")

    @staticmethod
    def _error_from(response: httpx.Response) -> HelpdeskError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        message = body.get("message") or body.get("problems") or response.text[:200]
        return HelpdeskError(response.status_code, body.get("error", "unknown"), str(message))

    def status(self) -> dict:
        """Mode of the account and how many tickets are open and handled."""
        return self._request("GET", "/status")

    def examples(self) -> list[dict]:
        """The labelled example tickets, used only by the eval."""
        return self._request("GET", "/examples")["examples"]

    def iter_tickets(self, status: str = "open", page_size: int = 10) -> Iterator[dict]:
        """Yield every ticket in the current set, following the pagination cursor."""
        cursor = None
        while True:
            params = {"limit": page_size, "status": status}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", "/tickets", params=params)
            yield from page["tickets"]
            cursor = page.get("next_cursor")
            if not cursor:
                return

    def get_ticket(self, ticket_id: str) -> dict:
        """One ticket, including how it was handled if it already was."""
        return self._request("GET", f"/tickets/{ticket_id}")
