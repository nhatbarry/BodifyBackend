"""Admin cost page: what the usage would cost on paid plans.

Every service is on a free plan, so these figures are a simulation with
example prices and no free allowance. The tests pin the arithmetic the page
relies on: fees are charged per day, every unit is billed from the first
one, and the daily series adds up to the line totals.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.services import cost_simulation as cs

GB = 1024**3


def _days(n: int, **usage: int) -> list[dict]:
    row = {"dau": 0, "mau": 0, "coach_replies": 0, "photo_ok": 0, "photo_failed": 0, **usage}
    start = date(2026, 11, 1)
    return [{"day": start + timedelta(days=i), **row} for i in range(n)]


def _line(result: dict, key: str) -> dict:
    return next(l for p in result["providers"] for l in p["lines"] if l["key"] == key)


def test_thirty_days_charge_one_full_monthly_fee():
    result = cs.build(_days(30), database_bytes=0)
    assert _line(result, "supabase_plan")["cost_usd"] == pytest.approx(cs.SUPABASE_PRO_MONTHLY)
    assert _line(result, "railway_plan")["cost_usd"] == pytest.approx(cs.RAILWAY_HOBBY_MONTHLY)


def test_fees_are_prorated_by_day():
    result = cs.build(_days(3), database_bytes=0)
    assert _line(result, "supabase_plan")["cost_usd"] == pytest.approx(cs.SUPABASE_PRO_MONTHLY / 10, abs=1e-4)


def test_nothing_is_free_the_first_unit_is_billed():
    # One active user, one coach reply, one photo: each costs something,
    # where the real plans' allowances would make all of it $0.
    result = cs.build(_days(1, dau=1, mau=1, coach_replies=1, photo_ok=1), database_bytes=GB)
    for key in ("supabase_db", "supabase_mau", "r2_class_a", "r2_class_b",
                "gemini_coach_in", "gemini_photo_out"):
        assert _line(result, key)["cost_usd"] > 0, key


def test_mau_is_billed_as_mau_months():
    # 1000 MAU held for 30 days is 1000 MAU for one month.
    result = cs.build(_days(30, mau=1000), database_bytes=0)
    line = _line(result, "supabase_mau")
    assert line["quantity"] == pytest.approx(1000)
    assert line["cost_usd"] == pytest.approx(1000 * cs.SUPABASE_PER_MAU)


def test_gemini_tokens_follow_the_assumptions():
    result = cs.build(_days(1, coach_replies=10, photo_ok=3, photo_failed=1), database_bytes=0)
    coach_requests = 10 * cs.COACH_REQUESTS_PER_REPLY
    assert result["ai"]["requests"] == coach_requests + 4
    assert result["ai"]["input_tokens"] == coach_requests * cs.COACH_TOKENS[0] + 4 * cs.PHOTO_TOKENS[0]
    # A failed analysis sent its photo but produced no draft.
    assert result["ai"]["output_tokens"] == coach_requests * cs.COACH_TOKENS[1] + 3 * cs.PHOTO_TOKENS[1]


def test_daily_series_adds_up_to_the_totals():
    result = cs.build(_days(14, dau=120, mau=900, coach_replies=7, photo_ok=2), database_bytes=400 * 1024**2)
    assert sum(d["total"] for d in result["daily"]) == pytest.approx(result["total_usd"], abs=0.01)
    for p in result["providers"]:
        assert sum(d[p["key"]] for d in result["daily"]) == pytest.approx(p["total_usd"], abs=0.01)
        assert sum(l["cost_usd"] for l in p["lines"]) == pytest.approx(p["total_usd"], abs=1e-3)


def test_route_needs_an_admin_and_answers_for_a_range(
    client: TestClient, account: Callable[[], dict], credentials: Callable[[], dict]
):
    from tests.conftest import API
    from tests.test_admin_mfa import _enroll_and_verify, _make_admin

    assert client.get(f"{API}/admin/costs/simulated", headers=account()).status_code == 403

    creds = credentials()
    _make_admin(creds["email"])
    _, _, tokens = _enroll_and_verify(client, creds["headers"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    r = client.get(f"{API}/admin/costs/simulated?from=2026-11-01&to=2026-11-07&scope=real", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simulated"] is True
    assert body["days"] == 7 and len(body["daily"]) == 7
    assert [p["key"] for p in body["providers"]] == ["supabase", "railway", "r2", "gemini"]
