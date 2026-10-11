"""Phase 8: daily health roll-ups pushed from the phone."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.conftest import API

TODAY = date.today().isoformat()


def _sync(client: TestClient, headers, *days, source="health_connect"):
    return client.put(
        f"{API}/health-data/daily", headers=headers, json={"source": source, "days": list(days)}
    )


def test_syncing_a_day_twice_updates_one_row(client: TestClient, owner):
    _sync(client, owner, {"day": TODAY, "steps": 3000})
    _sync(client, owner, {"day": TODAY, "steps": 5200})
    rows = client.get(
        f"{API}/health-data/daily", headers=owner, params={"from": TODAY, "to": TODAY}
    ).json()
    assert len(rows) == 1 and rows[0]["steps"] == 5200


def test_a_partial_sync_keeps_what_was_left_out(client: TestClient, owner):
    """Steps-only must not wipe a heart rate stored earlier."""
    _sync(client, owner, {"day": TODAY, "steps": 1000, "heart_rate_avg": 70, "heart_rate_min": 55, "heart_rate_max": 120})
    row = _sync(client, owner, {"day": TODAY, "steps": 2000}).json()[0]
    assert row["steps"] == 2000 and row["heart_rate_avg"] == 70


def test_days_are_private_to_their_owner(client: TestClient, owner, stranger):
    _sync(client, owner, {"day": TODAY, "steps": 999})
    seen = client.get(
        f"{API}/health-data/daily", headers=stranger, params={"from": TODAY, "to": TODAY}
    ).json()
    assert seen == []


@pytest.mark.parametrize(
    "day",
    [
        pytest.param({"day": TODAY, "steps": -1}, id="negative steps"),
        pytest.param({"day": TODAY, "heart_rate_avg": 400}, id="impossible bpm"),
        pytest.param({"day": TODAY, "heart_rate_min": 90, "heart_rate_max": 60}, id="min above max"),
        pytest.param({"day": TODAY, "heart_rate_avg": 200, "heart_rate_max": 120}, id="avg above max"),
        pytest.param({"day": (date.today() + timedelta(days=5)).isoformat()}, id="future"),
    ],
)
def test_nonsense_is_refused(client: TestClient, owner, day):
    assert _sync(client, owner, day).status_code == 422


def test_a_duplicate_day_in_one_request_is_refused(client: TestClient, owner):
    assert _sync(client, owner, {"day": TODAY, "steps": 1}, {"day": TODAY, "steps": 2}).status_code == 422


def test_unknown_source_is_refused(client: TestClient, owner):
    assert _sync(client, owner, {"day": TODAY, "steps": 1}, source="fitbit").status_code == 422


def test_requires_auth(client: TestClient):
    assert client.get(f"{API}/health-data/daily", params={"from": TODAY, "to": TODAY}).status_code in (401, 403)
