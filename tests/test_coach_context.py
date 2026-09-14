"""Phase 6: what the coach is told about the user.

These tests are about the block of facts, not about the model. They check two
things that decide whether the answers are any good:

- the numbers in the block match what the app's own screens would show, and
- an absent fact is stated as absent rather than left out, because a gap in
  the prompt is where a model invents a weight the user never recorded.

Like the chat tests, the model is stubbed - and here the stub is the whole
point, since it lets us read exactly what was handed to it.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.endpoints import ai_coach
from tests.conftest import API


@pytest.fixture
def captured(monkeypatch):
    """Records the context block the endpoint built for each question."""
    seen: list[str] = []

    async def fake_reply(history, question, context=None):
        seen.append(context or "")
        return "ok"

    monkeypatch.setattr(ai_coach, "generate_reply", fake_reply)
    return seen


def _ask(client: TestClient, owner, question: str = "Tôi nên tập gì?") -> None:
    response = client.post(
        f"{API}/ai-coach/sessions", headers=owner, json={"content": question}
    )
    assert response.status_code == 201, response.text


# ============================================================
# A user with nothing recorded
# ============================================================


def test_an_empty_account_is_described_as_empty(
    client: TestClient, owner, captured
):
    """Every section is present and says so. A missing section is a hole the
    model fills in with something plausible and wrong."""
    _ask(client, owner)
    context = captured[-1]

    assert "chưa ghi nhận" in context  # body metrics
    assert "chưa có buổi nào" in context  # training
    assert "chưa có giáo án" in context  # plan
    assert "Kỷ lục cá nhân: chưa có" in context


def test_the_users_name_is_included(client: TestClient, owner, captured):
    """A coach that cannot address the user by name reads like a form."""
    _ask(client, owner)
    assert "Pytest" in captured[-1]


# ============================================================
# A user who has trained
# ============================================================


def test_body_metrics_reach_the_coach_with_bmi(
    client: TestClient, owner, captured
):
    """BMI is computed here exactly as the app computes it - not stored, and
    not left for the model to work out from height and weight."""
    client.post(
        f"{API}/body-metrics",
        headers=owner,
        json={"height_cm": 170, "weight_kg": 70},
    )
    _ask(client, owner)

    context = captured[-1]
    assert "170cm" in context
    assert "70.0kg" in context
    assert "BMI 24.2" in context  # 70 / 1.7^2


def test_a_finished_workout_shows_up_in_the_summary(
    client: TestClient, owner, plan_with_day, captured
):
    session = client.post(
        f"{API}/workout-sessions",
        headers=owner,
        json={"workout_day_id": plan_with_day["day"]["workout_day_id"]},
    ).json()
    client.put(
        f"{API}/session-sets/{session['exercises'][0]['sets'][0]['session_set_id']}",
        headers=owner,
        json={"reps": 10, "weight_kg": 50, "completed": True},
    )
    client.post(
        f"{API}/workout-sessions/{session['session_id']}/finish",
        headers=owner,
        json={},
    )

    _ask(client, owner)
    context = captured[-1]

    assert "1 buổi" in context
    assert "500kg" in context  # 10 reps x 50 kg, the app's own volume figure
    # And the record that finishing just created.
    assert "Kỷ lục cá nhân:" in context
    assert "chưa có" not in context.split("Kỷ lục cá nhân:")[1]


def test_the_active_plan_and_todays_session_are_named(
    client: TestClient, owner, plan_with_day, captured
):
    """"Hôm nay tập gì" is the question this exists to answer."""
    activated = client.put(
        f"{API}/workout-plans/{plan_with_day['plan']['plan_id']}/active",
        headers=owner,
        json={"is_active": True},
    )
    # Asserted, because the first version of this test sent no body, got a
    # 422, and then "failed" on the context - which looked like a bug in the
    # context builder rather than in the test.
    assert activated.status_code == 200, activated.text

    _ask(client, owner)

    context = captured[-1]
    assert "Pytest Plan" in context
    assert "Hôm nay:" in context


def test_today_lists_the_exercises_not_the_days_name(
    client: TestClient, owner, plan_with_day, captured
):
    """Days created from the calendar are named with the ISO date - an
    identifier, not a label. The first version put that name in the prompt and
    the coach answered "bạn có buổi 2026-09-14" and then admitted it had no
    idea what the user was training."""
    plan_id = plan_with_day["plan"]["plan_id"]
    client.put(
        f"{API}/workout-plans/{plan_id}/active",
        headers=owner,
        json={"is_active": True},
    )

    today = date.today().isoformat()
    scheduled = client.put(
        f"{API}/workout-plans/{plan_id}/dates/{today}/exercises",
        headers=owner,
        json={"exercise_ids": [1]},
    )
    assert scheduled.status_code == 200, scheduled.text

    _ask(client, owner)
    line = next(
        l for l in captured[-1].splitlines() if l.startswith("- Hôm nay:")
    )

    assert today not in line, "the ISO day name leaked into the prompt"
    # Exercise 1 is Bench Press in the seeded catalog; whatever it is called,
    # the line has to name something the user would recognise.
    assert len(line) > len("- Hôm nay: ") + 3


# ============================================================
# Size
# ============================================================


def test_the_context_stays_small(client: TestClient, owner, plan_with_day, captured):
    """Every request pays for this block, and on the free tier the token
    ceiling binds before the request ceiling does. Summaries, not tables."""
    _ask(client, owner)
    context = captured[-1]

    assert len(context) < 1200, f"context grew to {len(context)} chars"
    # One line per fact, not a dump.
    assert len(context.splitlines()) <= 10


# ============================================================
# Privacy
# ============================================================


def test_one_users_data_never_reaches_anothers_coach(
    client: TestClient, owner, stranger, captured
):
    client.post(
        f"{API}/body-metrics",
        headers=owner,
        json={"height_cm": 180, "weight_kg": 95},
    )
    _ask(client, stranger)

    context = captured[-1]
    assert "95" not in context
    assert "180cm" not in context
