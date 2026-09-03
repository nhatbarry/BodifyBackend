"""Body metrics: the numbers Phase 4 will train on."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from tests.conftest import API


def test_a_normal_measurement_is_accepted(client: TestClient, owner):
    response = client.post(
        f"{API}/body-metrics", headers=owner, json={"height_cm": 172, "weight_kg": 68.5}
    )
    assert response.status_code == 201
    assert response.json()["bmi"] == pytest.approx(23.15, abs=0.01)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"weight_kg": 6776}, id="the 6776 kg that used to be accepted"),
        pytest.param({"weight_kg": 0.5}, id="below any newborn"),
        pytest.param({"height_cm": 900}, id="taller than a house"),
        pytest.param({"height_cm": 5}, id="shorter than a hand"),
    ],
)
def test_absurd_measurements_are_refused(client: TestClient, owner, payload):
    """The column only caps the digit count, so this has to be the API's job.
    One fat-fingered entry poisons every average it lands in."""
    assert client.post(f"{API}/body-metrics", headers=owner, json=payload).status_code == 422


def test_a_future_reading_is_refused(client: TestClient, owner):
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = client.post(
        f"{API}/body-metrics",
        headers=owner,
        json={"weight_kg": 70, "recorded_at": tomorrow},
    )
    assert response.status_code == 422


def test_backdating_is_allowed(client: TestClient, owner):
    """People catch up on a week of weigh-ins; that is not an error."""
    last_week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    response = client.post(
        f"{API}/body-metrics",
        headers=owner,
        json={"weight_kg": 70, "recorded_at": last_week},
    )
    assert response.status_code == 201


def test_an_empty_measurement_is_refused(client: TestClient, owner):
    assert client.post(f"{API}/body-metrics", headers=owner, json={}).status_code == 422


def test_bmi_is_computed_not_stored(client: TestClient, owner):
    """A stored BMI would drift from its own inputs the moment either changes."""
    created = client.post(
        f"{API}/body-metrics", headers=owner, json={"height_cm": 180, "weight_kg": 81}
    ).json()
    assert created["bmi"] == pytest.approx(25.0, abs=0.01)

    # Height alone gives nothing to divide into.
    only_height = client.post(
        f"{API}/body-metrics", headers=owner, json={"height_cm": 180}
    ).json()
    assert only_height["bmi"] is None


def test_another_users_metric_answers_404(client: TestClient, owner, stranger):
    metric = client.post(
        f"{API}/body-metrics", headers=owner, json={"weight_kg": 70}
    ).json()
    response = client.delete(
        f"{API}/body-metrics/{metric['metric_id']}", headers=stranger
    )
    assert response.status_code == 404, "403 would confirm the row exists"
