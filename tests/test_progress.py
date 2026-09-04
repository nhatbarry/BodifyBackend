"""Phase 4: personal records, achievements and the progress figures.

Every test here pins a decision that was argued, not a coincidence of the
current code - above all that finishing a session twice, which reopening now
makes ordinary, must not award anything twice.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.conftest import API
from app.services.personal_records import estimated_1rm
from app.services.progress import SetRow, set_performance


def _start(client: TestClient, owner, day_id: int | None = None) -> dict:
    body = {"workout_day_id": day_id} if day_id else {}
    response = client.post(f"{API}/workout-sessions", headers=owner, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _log(client: TestClient, owner, set_id: int, **values) -> None:
    response = client.put(
        f"{API}/session-sets/{set_id}",
        headers=owner,
        json={"completed": True, **values},
    )
    assert response.status_code == 200, response.text


def _finish(client: TestClient, owner, session_id: int) -> None:
    response = client.post(
        f"{API}/workout-sessions/{session_id}/finish", headers=owner, json={}
    )
    assert response.status_code == 200, response.text


# ============================================================
# The formulas
# ============================================================


def test_one_rep_max_of_a_single_rep_is_the_weight_lifted():
    """Epley returns w x 1.033 for one rep, which claims the user can lift
    more than they just proved. A one-rep max is the weight."""
    assert estimated_1rm(Decimal("100"), 1) == Decimal("100")
    assert estimated_1rm(Decimal("100"), 10) > Decimal("100")


def test_performance_uses_volume_when_the_plan_named_a_weight():
    """10 reps at 36 kg against a planned 10 at 40 is not a full session's
    work, and comparing reps alone would call it 100%."""
    row = SetRow("reps", 10, Decimal("36"), None, None, 10, Decimal("40"),
                 None, None, True)
    assert round(set_performance(row) * 100, 1) == 90.0


def test_performance_of_a_hold_compares_seconds():
    """A Plank measured against a Bench Press's formula produces a number that
    is arithmetically fine and means nothing."""
    row = SetRow("duration", None, None, 52, None, None, None, 60, None, True)
    assert round(set_performance(row) * 100, 1) == 86.7


def test_a_set_with_no_target_has_no_performance():
    """A set added mid-workout was never planned. It is a real performance and
    stays in the history, but it cannot be scored against a plan."""
    row = SetRow("reps", 8, None, None, None, None, None, None, None, True)
    assert set_performance(row) is None
    assert row.has_target is False


# ============================================================
# Personal records
# ============================================================


def test_finishing_records_a_personal_best(client: TestClient, owner, plan_with_day):
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    set_id = session["exercises"][0]["sets"][0]["session_set_id"]
    _log(client, owner, set_id, reps=10, weight_kg=50)
    _finish(client, owner, session["session_id"])

    bests = client.get(f"{API}/personal-records", headers=owner).json()
    by_type = {b["record_type"]: b for b in bests}

    assert float(by_type["max_weight"]["value"]) == 50.0
    assert float(by_type["max_reps"]["value"]) == 10.0
    assert float(by_type["max_volume"]["value"]) == 500.0
    assert "estimated_1rm" in by_type


def test_finishing_the_same_session_twice_does_not_duplicate_the_record(
    client: TestClient, owner, plan_with_day
):
    """Reopening made a second finish ordinary. The second pass sees the same
    sets, and a record only counts if it beats the standing best - so the
    history must not grow a second identical row."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    set_id = session["exercises"][0]["sets"][0]["session_set_id"]

    _log(client, owner, set_id, reps=10, weight_kg=50)
    _finish(client, owner, session_id)

    client.post(f"{API}/workout-sessions/{session_id}/reopen", headers=owner)
    _finish(client, owner, session_id)

    history = client.get(
        f"{API}/personal-records/1?record_type=max_weight", headers=owner
    ).json()
    assert len(history) == 1, "the same 50 kg must not be recorded twice"


def test_beating_a_record_appends_to_the_history(
    client: TestClient, owner, plan_with_day
):
    """40 -> 50 is the series a progress chart plots, so both rows stay."""
    day_id = plan_with_day["day"]["workout_day_id"]

    first = _start(client, owner, day_id)
    _log(client, owner, first["exercises"][0]["sets"][0]["session_set_id"],
         reps=5, weight_kg=40)
    _finish(client, owner, first["session_id"])

    second = _start(client, owner, day_id)
    _log(client, owner, second["exercises"][0]["sets"][0]["session_set_id"],
         reps=5, weight_kg=50)
    _finish(client, owner, second["session_id"])

    history = client.get(
        f"{API}/personal-records/1?record_type=max_weight", headers=owner
    ).json()
    assert [float(row["value"]) for row in history] == [40.0, 50.0]

    bests = {
        b["record_type"]: b
        for b in client.get(f"{API}/personal-records", headers=owner).json()
    }
    assert float(bests["max_weight"]["value"]) == 50.0


def test_a_lighter_session_does_not_lower_the_record(
    client: TestClient, owner, plan_with_day
):
    """A record is a ceiling, not the most recent value."""
    day_id = plan_with_day["day"]["workout_day_id"]

    heavy = _start(client, owner, day_id)
    _log(client, owner, heavy["exercises"][0]["sets"][0]["session_set_id"],
         reps=5, weight_kg=60)
    _finish(client, owner, heavy["session_id"])

    light = _start(client, owner, day_id)
    _log(client, owner, light["exercises"][0]["sets"][0]["session_set_id"],
         reps=5, weight_kg=30)
    _finish(client, owner, light["session_id"])

    bests = {
        b["record_type"]: b
        for b in client.get(f"{API}/personal-records", headers=owner).json()
    }
    assert float(bests["max_weight"]["value"]) == 60.0


def test_an_abandoned_session_sets_no_record(
    client: TestClient, owner, plan_with_day
):
    """Abandoning is the user saying it did not happen. A record from an
    attempt they never claimed would be one they cannot account for."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    _log(client, owner, session["exercises"][0]["sets"][0]["session_set_id"],
         reps=10, weight_kg=100)
    client.post(
        f"{API}/workout-sessions/{session['session_id']}/finish",
        headers=owner,
        json={"abandoned": True},
    )

    assert client.get(f"{API}/personal-records", headers=owner).json() == []


# ============================================================
# Achievements
# ============================================================


def test_the_first_workout_unlocks_its_achievement(
    client: TestClient, owner, plan_with_day
):
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    _log(client, owner, session["exercises"][0]["sets"][0]["session_set_id"],
         reps=10, weight_kg=40)
    _finish(client, owner, session["session_id"])

    by_code = {
        a["code"]: a
        for a in client.get(f"{API}/achievements", headers=owner).json()
    }
    assert by_code["WORKOUT_FIRST"]["unlocked"] is True
    assert by_code["WORKOUT_10"]["unlocked"] is False
    assert by_code["WORKOUT_10"]["current_value"] == 1.0


def test_refinishing_does_not_inflate_the_workout_count(
    client: TestClient, owner, plan_with_day
):
    """The count is a query over completed sessions, not a counter that gets
    incremented - so reopening and finishing again still means one workout."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    _log(client, owner, session["exercises"][0]["sets"][0]["session_set_id"],
         reps=10, weight_kg=40)
    _finish(client, owner, session_id)

    for _ in range(3):
        client.post(f"{API}/workout-sessions/{session_id}/reopen", headers=owner)
        _finish(client, owner, session_id)

    by_code = {
        a["code"]: a
        for a in client.get(f"{API}/achievements", headers=owner).json()
    }
    assert by_code["WORKOUT_7"]["current_value"] == 1.0
    assert by_code["WORKOUT_7"]["unlocked"] is False


def test_a_locked_achievement_still_reports_progress(client: TestClient, owner):
    """A badge you can see yourself approaching is the reason to come back;
    hiding it until it unlocks throws that away."""
    catalog = client.get(f"{API}/achievements", headers=owner).json()
    assert catalog, "the catalog is seeded by ddl_phase_4.sql"
    assert all(a["unlocked"] is False for a in catalog)
    assert all(a["current_value"] is not None for a in catalog)


# ============================================================
# Progress
# ============================================================


def test_completion_ignores_sets_the_plan_never_asked_for(
    client: TestClient, owner, plan_with_day
):
    """Three planned sets, two done, plus one added mid-workout. The extra is
    real work and stays in the history, but counting it would make completion
    depend on how much unplanned work the user did."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    sets = session["exercises"][0]["sets"]

    _log(client, owner, sets[0]["session_set_id"], reps=10, weight_kg=40)
    _log(client, owner, sets[1]["session_set_id"], reps=10, weight_kg=40)

    client.post(
        f"{API}/session-exercises/{session['exercises'][0]['session_exercise_id']}/sets",
        headers=owner,
        json={"reps": 12, "weight_kg": 30, "completed": True},
    )
    _finish(client, owner, session["session_id"])

    summary = client.get(f"{API}/progress/summary", headers=owner).json()
    assert summary["planned_sets"] == 3
    assert summary["completed_planned_sets"] == 2
    assert round(summary["completion_percent"], 1) == 66.7


def test_progression_reports_the_best_set_of_each_day(
    client: TestClient, owner, plan_with_day
):
    """A heavy top set followed by lighter backoff work is progress, not a
    decline, so the day is represented by its best set."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    sets = session["exercises"][0]["sets"]
    _log(client, owner, sets[0]["session_set_id"], reps=5, weight_kg=60)
    _log(client, owner, sets[1]["session_set_id"], reps=10, weight_kg=30)
    _finish(client, owner, session["session_id"])

    points = client.get(f"{API}/progress/exercises/1", headers=owner).json()["points"]
    assert len(points) == 1
    assert points[0]["best_weight_kg"] == 60.0
    assert points[0]["volume_kg"] == 600.0  # 5x60 + 10x30


def test_progression_says_how_the_exercise_is_measured(
    client: TestClient, owner
):
    """The client needs to know whether to plot kilograms, seconds or metres;
    plotting all of them gives three flat lines and one real one."""
    plank = client.get(f"{API}/progress/exercises/8", headers=owner).json()
    assert plank["tracking_type"] == "duration"

    bench = client.get(f"{API}/progress/exercises/1", headers=owner).json()
    assert bench["tracking_type"] == "reps"


def test_progress_range_is_validated(client: TestClient, owner):
    response = client.get(
        f"{API}/progress/summary?from=2026-09-30&to=2026-09-01", headers=owner
    )
    assert response.status_code == 422


def test_a_fractional_record_does_not_beat_itself(
    client: TestClient, owner, plan_with_day
):
    """Epley on 35 kg x 8 gives 44.3333..., and NUMERIC(10,2) stores 44.33.

    Comparing the unrounded candidate against its own stored form made every
    finish look like a new 1RM, so re-finishing one session four times left
    four identical rows in the history. Found by re-finishing a real session,
    not by a test - the earlier tests all used whole numbers.
    """
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    _log(client, owner, session["exercises"][0]["sets"][0]["session_set_id"],
         reps=8, weight_kg=35)
    _finish(client, owner, session_id)

    for _ in range(3):
        client.post(f"{API}/workout-sessions/{session_id}/reopen", headers=owner)
        _finish(client, owner, session_id)

    history = client.get(
        f"{API}/personal-records/1?record_type=estimated_1rm", headers=owner
    ).json()
    assert len(history) == 1, f"1RM recorded {len(history)} times for one lift"
    assert float(history[0]["value"]) == 44.33


def test_frequency_is_not_extrapolated_from_a_single_day(
    client: TestClient, owner, plan_with_day
):
    """Two sessions on the only day somebody has trained is arithmetically 14
    a week, and is not a fact about their training."""
    day_id = plan_with_day["day"]["workout_day_id"]
    for _ in range(2):
        session = _start(client, owner, day_id)
        _log(client, owner, session["exercises"][0]["sets"][0]["session_set_id"],
             reps=10, weight_kg=40)
        _finish(client, owner, session["session_id"])

    summary = client.get(f"{API}/progress/summary", headers=owner).json()
    assert summary["session_count"] == 2
    assert summary["workouts_per_week"] == 2.0
