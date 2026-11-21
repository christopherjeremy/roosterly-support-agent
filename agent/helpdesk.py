"""Client for the Roosterly helpdesk API.

All requests go through HelpdeskClient._request, so error handling and retries live in one place.
"""

from collections.abc import Iterator

import httpx

from agent.config import Settings


class HelpdeskError(Exception):
    """An error response from the helpdesk, with its HTTP status and error code."""

    def __init__(self, status_code: int, error: str, message: str):
        super().__init__(f"{status_code} {error}: {message}")
        self.status_code = status_code
        self.error = error
        self.message = message


class HelpdeskClient:
    def __init__(self, settings: Settings, timeout: float = 30.0):
        headers = {
            "Authorization": f"Bearer {settings.helpdesk_api_key}",
            "X-Candidate-Email": settings.candidate_email,
        }
        if settings.candidate_name:
            headers["X-Candidate-Name"] = settings.candidate_name
        self._http = httpx.Client(base_url=settings.helpdesk_base_url, headers=headers, timeout=timeout)

    def __enter__(self) -> "HelpdeskClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> dict:
        response = self._http.request(method, path, **kwargs)
        if response.is_success:
            return response.json()

        try:
            body = response.json()
        except ValueError:
            body = {}
        message = body.get("message") or body.get("problems") or response.text[:200]
        raise HelpdeskError(response.status_code, body.get("error", "unknown"), str(message))

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
