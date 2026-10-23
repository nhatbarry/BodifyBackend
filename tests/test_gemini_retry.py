"""The Gemini client rides out Google's short "high demand" 503s.

No network and no database: the transport is faked, and the pauses between
attempts are skipped. What is pinned is which answers are retried, how often,
and that the caller still gets the last response when retrying runs out.
"""

import asyncio

import httpx
import pytest

from app.services import gemini


def _run(statuses: list[int]) -> tuple[int, int]:
    """Serves `statuses` in order and returns (final status, calls made)."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        status = statuses[min(calls, len(statuses) - 1)]
        calls += 1
        return httpx.Response(status, json={})

    async def go() -> int:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response = await gemini._post_with_retry(client, "https://gemini.test", {})
            return response.status_code

    return asyncio.run(go()), calls


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(gemini.asyncio, "sleep", instant)


def test_a_503_followed_by_an_answer_is_an_answer():
    assert _run([503, 200]) == (200, 2)


def test_overload_is_retried_twice_then_reported():
    # Three attempts in all; the last 503 reaches the caller's error mapping.
    assert _run([503, 503, 503, 200]) == (503, 3)


@pytest.mark.parametrize("status", [400, 403, 404, 429])
def test_a_request_the_server_refused_is_not_repeated(status):
    # 429 especially: retrying a spent quota spends nothing but time.
    assert _run([status, 200]) == (status, 1)


def test_no_retry_starts_past_the_budget(monkeypatch):
    # The first call "takes" 20 s: a 1 s pause after it would end past the
    # 20 s budget, so the 503 is returned rather than waited out again.
    clock = iter(range(0, 1000, 20))
    monkeypatch.setattr(gemini.time, "monotonic", lambda: next(clock))
    assert _run([503, 200]) == (503, 1)
