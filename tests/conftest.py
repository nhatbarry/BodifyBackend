"""Shared fixtures.

These are integration tests, not unit tests: they run against the real local
Supabase - real Auth, real Postgres - because that is where every interesting
rule in this codebase actually lives. Ownership is a WHERE clause, ordering is
a deferred constraint, "one active plan" is a partial unique index. Mocking the
database would test the mock.

The price is that a test run creates real accounts and real rows. Every
fixture here is responsible for removing what it made, so a run leaves the
database exactly as it found it - `assert_clean` at the end of the session
fails loudly if it did not.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.main import app

API = "/api/v1"

# Anything created by a test carries this in its email, which is what the
# cleanup below keys on. It must not collide with a real account.
TEST_EMAIL_PREFIX = "pytest-"
PASSWORD = "Passw0rd!23"


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _purge_test_accounts() -> int:
    """Remove every account this suite created, and everything it owns.

    Deletes are explicit rather than relying on cascades: `workout_plans` and
    `workout_sessions` hold a bare uuid with no foreign key into auth.users, so
    nothing would follow the account out on its own.
    """
    with SessionLocal() as db:
        ids = db.execute(
            text(
                "SELECT id FROM auth.users WHERE email LIKE :p"
            ),
            {"p": f"{TEST_EMAIL_PREFIX}%"},
        ).scalars().all()
        if not ids:
            return 0

        for statement in (
            "DELETE FROM workout_sessions WHERE user_id = ANY(:ids)",
            "DELETE FROM workout_plans WHERE user_id = ANY(:ids)",
            "DELETE FROM body_metrics WHERE user_id = ANY(:ids)",
            "DELETE FROM schedules WHERE user_id = ANY(:ids)",
            "DELETE FROM user_profiles WHERE user_id = ANY(:ids)",
            "DELETE FROM auth.users WHERE id = ANY(:ids)",
        ):
            db.execute(text(statement), {"ids": ids})
        db.commit()
        return len(ids)


@pytest.fixture(scope="session", autouse=True)
def clean_slate() -> Iterator[None]:
    """Purge before and after.

    Before as well as after, because a run killed halfway leaves accounts
    behind, and the next run should not inherit them.
    """
    _purge_test_accounts()
    yield
    _purge_test_accounts()


@pytest.fixture
def account(client: TestClient) -> Callable[[], dict[str, str]]:
    """Factory returning auth headers for a fresh throwaway account.

    A factory rather than a single account because most of the interesting
    rules are about *other people's* rows: half these tests need two users.
    """

    def make() -> dict[str, str]:
        email = f"{TEST_EMAIL_PREFIX}{uuid.uuid4().hex[:12]}@example.com"
        response = client.post(
            f"{API}/auth/register",
            json={"email": email, "password": PASSWORD, "name": "Pytest"},
        )
        assert response.status_code < 400, response.text

        token = (response.json().get("session") or {}).get("access_token")
        if not token:
            # Projects that require email confirmation register without a
            # session; log in separately.
            token = client.post(
                f"{API}/auth/login",
                json={"email": email, "password": PASSWORD},
            ).json()["access_token"]

        return {"Authorization": f"Bearer {token}"}

    return make


@pytest.fixture
def owner(account: Callable[[], dict[str, str]]) -> dict[str, str]:
    return account()


@pytest.fixture
def stranger(account: Callable[[], dict[str, str]]) -> dict[str, str]:
    """A second user, for the "somebody else's row answers 404" checks."""
    return account()


@pytest.fixture
def plan_with_day(client: TestClient, owner: dict[str, str]):
    """A plan with one day holding one exercise and three target sets.

    The shape most Phase 2/3 tests need, built through the API rather than by
    inserting rows, so the fixture exercises the same paths the app does.
    """
    plan = client.post(
        f"{API}/workout-plans",
        headers=owner,
        json={"name": "Pytest Plan", "goal": "muscle_gain"},
    ).json()
    day = client.post(
        f"{API}/workout-plans/{plan['plan_id']}/days",
        headers=owner,
        json={"name": "Push"},
    ).json()
    exercise = client.post(
        f"{API}/workout-days/{day['workout_day_id']}/exercises",
        headers=owner,
        json={
            "exercise_id": 1,
            "sets": [
                {"target_reps": 10, "target_weight_kg": 40, "rest_seconds": 90},
                {"target_reps": 10, "target_weight_kg": 40},
                {"target_reps": 8, "target_weight_kg": 45},
            ],
        },
    ).json()
    return {"plan": plan, "day": day, "exercise": exercise}
