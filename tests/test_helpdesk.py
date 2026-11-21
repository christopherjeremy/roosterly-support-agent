"""Tests for the helpdesk client's error handling, using a fake transport instead of the real API."""

import httpx
import pytest

from agent.config import Settings
from agent.helpdesk import MAX_ATTEMPTS, HelpdeskClient, HelpdeskError, HelpdeskOutcomeUnknown

SETTINGS = Settings(
    helpdesk_base_url="https://helpdesk.test",
    helpdesk_api_key="test-key",
    candidate_email="candidate@example.com",
    candidate_name="",
    gemini_api_key="",
    model="",
)


def make_client(responses):
    """Build a client whose transport replays `responses` in order and records each request.

    Each item is either an httpx.Response or an exception to raise.
    """
    calls, sleeps = [], []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = HelpdeskClient(SETTINGS, transport=httpx.MockTransport(handler), sleep=sleeps.append)
    return client, calls, sleeps


def error(status: int, code: str, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": code, "message": code}, headers=headers)


def ok(body: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=body or {"ok": True})


def retry_sleeps(sleeps):
    """Sleeps longer than the rate-limit pause are retry waits."""
    return [s for s in sleeps if s > 0.3]


def test_503_is_retried_with_backoff():
    client, calls, sleeps = make_client([error(503, "service_unavailable")] * 2 + [ok()])
    assert client.status() == {"ok": True}
    assert len(calls) == 3
    assert retry_sleeps(sleeps) == [0.5, 1.0]


def test_503_on_write_is_retried_because_nothing_was_saved():
    client, calls, _ = make_client([error(503, "service_unavailable"), ok()])
    assert client._request("POST", "/tickets/T001/close", json={}) == {"ok": True}
    assert len(calls) == 2


def test_429_waits_for_retry_after():
    client, calls, sleeps = make_client([error(429, "rate_limited", {"Retry-After": "2"}), ok()])
    client.status()
    assert len(calls) == 2
    assert 2.0 in sleeps


def test_timeout_on_read_is_retried():
    client, calls, _ = make_client([httpx.ReadTimeout("timed out"), ok()])
    assert client.status() == {"ok": True}
    assert len(calls) == 2


def test_timeout_on_write_is_not_retried():
    client, calls, _ = make_client([httpx.ReadTimeout("timed out"), ok()])
    with pytest.raises(HelpdeskOutcomeUnknown):
        client._request("POST", "/tickets/T001/reply", json={})
    assert len(calls) == 1


def test_409_is_raised_without_retry():
    client, calls, _ = make_client([error(409, "already_handled"), ok()])
    with pytest.raises(HelpdeskError) as raised:
        client._request("POST", "/tickets/T001/reply", json={})
    assert raised.value.status_code == 409
    assert raised.value.error == "already_handled"
    assert len(calls) == 1


def test_persistent_503_gives_up_after_max_attempts():
    client, calls, _ = make_client([error(503, "service_unavailable")] * MAX_ATTEMPTS)
    with pytest.raises(HelpdeskError) as raised:
        client.status()
    assert raised.value.status_code == 503
    assert len(calls) == MAX_ATTEMPTS


def test_requests_are_spaced_to_respect_the_rate_limit():
    client, _, sleeps = make_client([ok(), ok(), ok()])
    for _ in range(3):
        client.status()
    assert any(0 < s <= 0.25 for s in sleeps)
