"""Phase 3: recorded workouts, pausing, and history that stays put."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.conftest import API


def _start(client: TestClient, owner, day_id: int | None = None) -> dict:
    body = {"workout_day_id": day_id} if day_id else {}
    response = client.post(f"{API}/workout-sessions", headers=owner, json=body)
    assert response.status_code == 201, response.text
    return response.json()


# ============================================================
# Starting
# ============================================================


def test_starting_from_a_day_copies_the_plan_in(client: TestClient, owner, plan_with_day):
    """Otherwise recording a planned workout is one request per set before the
    user has lifted anything."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])

    assert session["plan_name"] == "Pytest Plan"
    assert session["day_name"] == "Push"
    exercise = session["exercises"][0]
    assert len(exercise["sets"]) == 3

    first = exercise["sets"][0]
    assert first["target_reps"] == 10, "the plan's numbers should come across"
    assert first["reps"] is None, "nothing has been performed yet"
    assert first["completed"] is False


def test_only_one_workout_can_be_in_progress(client: TestClient, owner):
    _start(client, owner)
    second = client.post(f"{API}/workout-sessions", headers=owner, json={})
    assert second.status_code == 409


# ============================================================
# The plan is a snapshot, not a live reading
# ============================================================


def test_history_survives_the_plan_being_deleted(client: TestClient, owner, plan_with_day):
    """The rule that shaped the whole schema: a plan is edited for months, a
    finished session is a statement about one afternoon."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    set_id = session["exercises"][0]["sets"][0]["session_set_id"]
    client.put(
        f"{API}/session-sets/{set_id}",
        headers=owner,
        json={"reps": 12, "weight_kg": 42.5},
    )
    client.post(f"{API}/workout-sessions/{session_id}/finish", headers=owner, json={})

    client.delete(
        f"{API}/workout-plans/{plan_with_day['plan']['plan_id']}", headers=owner
    )

    after = client.get(f"{API}/workout-sessions/{session_id}", headers=owner)
    assert after.status_code == 200
    body = after.json()
    assert body["plan_name"] == "Pytest Plan", "the snapshot should outlive the plan"
    assert body["day_name"] == "Push"
    assert body["workout_day_id"] is None, "SET NULL, not CASCADE"
    assert body["exercises"][0]["exercise_name"]
    recorded = body["exercises"][0]["sets"][0]
    assert recorded["target_reps"] == 10, "the planned number is a copy, not a lookup"
    assert recorded["reps"] == 12
    assert recorded["workout_exercise_set_id"] is None


def test_planned_and_performed_are_kept_apart(client: TestClient, owner, plan_with_day):
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    target = session["exercises"][0]["sets"][2]  # planned 8 x 45

    updated = client.put(
        f"{API}/session-sets/{target['session_set_id']}",
        headers=owner,
        json={"reps": 8, "weight_kg": 47.5},
    ).json()

    assert float(updated["target_weight_kg"]) == 45.0
    assert float(updated["weight_kg"]) == 47.5


# ============================================================
# Immutability, with one carve-out
# ============================================================


def test_a_finished_session_takes_corrections_but_no_new_work(
    client: TestClient, owner, plan_with_day
):
    """Typing 400 kg for 40 is common, and a history nobody can fix is worse
    than one that can be. Adding to a finished session is a different thing."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    exercise = session["exercises"][0]
    set_id = exercise["sets"][0]["session_set_id"]

    client.post(f"{API}/workout-sessions/{session_id}/finish", headers=owner, json={})

    assert (
        client.post(
            f"{API}/session-exercises/{exercise['session_exercise_id']}/sets",
            headers=owner,
            json={"reps": 5},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"{API}/workout-sessions/{session_id}/exercises",
            headers=owner,
            json={"exercise_id": 2},
        ).status_code
        == 409
    )

    corrected = client.put(
        f"{API}/session-sets/{set_id}", headers=owner, json={"weight_kg": 42.5}
    )
    assert corrected.status_code == 200
    assert float(corrected.json()["weight_kg"]) == 42.5
    assert float(corrected.json()["target_weight_kg"]) == 40.0, (
        "a correction must not rewrite what the plan said"
    )


def test_finishing_twice_is_refused(client: TestClient, owner):
    session = _start(client, owner)
    sid = session["session_id"]
    assert client.post(f"{API}/workout-sessions/{sid}/finish", headers=owner, json={}).status_code == 200
    assert client.post(f"{API}/workout-sessions/{sid}/finish", headers=owner, json={}).status_code == 409


# ============================================================
# Pausing
# ============================================================


def test_pausing_stops_the_clock_and_resuming_starts_it_again(client: TestClient, owner):
    """A workout is not one continuous block. Counting a phone call as
    training makes every recorded duration a lie."""
    session = _start(client, owner)
    sid = session["session_id"]

    time.sleep(2)
    running = client.get(f"{API}/workout-sessions/{sid}", headers=owner).json()
    assert running["duration_seconds"] >= 2

    paused = client.post(f"{API}/workout-sessions/{sid}/pause", headers=owner).json()
    assert paused["status"] == "paused"
    frozen = paused["duration_seconds"]

    time.sleep(3)
    still = client.get(f"{API}/workout-sessions/{sid}", headers=owner).json()
    assert still["duration_seconds"] == frozen, "the clock kept running while paused"

    resumed = client.post(f"{API}/workout-sessions/{sid}/resume", headers=owner).json()
    assert resumed["status"] == "in_progress"
    assert resumed["paused_seconds"] >= 3, "the break was not banked"
    assert abs(resumed["duration_seconds"] - frozen) <= 1


def test_pausing_does_not_free_the_one_workout_slot(client: TestClient, owner):
    session = _start(client, owner)
    client.post(f"{API}/workout-sessions/{session['session_id']}/pause", headers=owner)

    assert client.post(f"{API}/workout-sessions", headers=owner, json={}).status_code == 409


def test_a_paused_session_still_accepts_sets(client: TestClient, owner, plan_with_day):
    """Refusing to record a set the user just did would be perverse; pause is
    about the clock, not about locking the data."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    client.post(f"{API}/workout-sessions/{session['session_id']}/pause", headers=owner)

    response = client.post(
        f"{API}/session-exercises/{session['exercises'][0]['session_exercise_id']}/sets",
        headers=owner,
        json={"reps": 10, "weight_kg": 20},
    )
    assert response.status_code == 201


def test_pause_and_resume_are_idempotent(client: TestClient, owner):
    session = _start(client, owner)
    sid = session["session_id"]

    first = client.post(f"{API}/workout-sessions/{sid}/pause", headers=owner).json()
    second = client.post(f"{API}/workout-sessions/{sid}/pause", headers=owner).json()
    assert second["duration_seconds"] == first["duration_seconds"]

    client.post(f"{API}/workout-sessions/{sid}/resume", headers=owner)
    assert client.post(f"{API}/workout-sessions/{sid}/resume", headers=owner).status_code == 200


# ============================================================
# History
# ============================================================


def test_history_counts_only_what_actually_happened(client: TestClient, owner, plan_with_day):
    """An abandoned session is not a workout; averaging it into a month's
    volume would understate every number on the screen."""
    done = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    client.put(
        f"{API}/session-sets/{done['exercises'][0]['sets'][0]['session_set_id']}",
        headers=owner,
        json={"reps": 10, "weight_kg": 40},
    )
    client.post(f"{API}/workout-sessions/{done['session_id']}/finish", headers=owner, json={})

    quit_early = _start(client, owner)
    client.post(
        f"{API}/workout-sessions/{quit_early['session_id']}/finish",
        headers=owner,
        json={"abandoned": True},
    )

    history = client.get(f"{API}/workout-history", headers=owner).json()
    assert history["totals"]["session_count"] == 1
    assert history["totals"]["total_reps"] == 10
    # 10 reps x 40 kg
    assert float(history["totals"]["total_volume_kg"]) == 400.0

    # The abandoned one is kept, just not counted.
    assert client.get(f"{API}/workout-sessions", headers=owner).json()["total"] == 2


def test_volume_is_summed_per_set_not_multiplied_afterwards(
    client: TestClient, owner, plan_with_day
):
    """sum(reps x weight), not sum(reps) x sum(weight) - the difference
    between a real number and a meaningless one."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    sets = session["exercises"][0]["sets"]

    client.put(
        f"{API}/session-sets/{sets[0]['session_set_id']}",
        headers=owner,
        json={"reps": 10, "weight_kg": 40},
    )
    client.put(
        f"{API}/session-sets/{sets[1]['session_set_id']}",
        headers=owner,
        json={"reps": 5, "weight_kg": 100},
    )

    body = client.get(
        f"{API}/workout-sessions/{session['session_id']}", headers=owner
    ).json()
    # 400 + 500, not (15) x (140) = 2100
    assert float(body["summary"]["total_volume_kg"]) == 900.0


# ============================================================
# Reopening
# ============================================================


def test_a_finished_session_can_be_reopened_and_added_to(
    client: TestClient, owner, plan_with_day
):
    """"Finish" is the user saying they were done, not a lock.

    One more set after racking the bar is ordinary; forcing it into a second
    session would show the afternoon as two workouts in the history.
    """
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    exercise_id = session["exercises"][0]["session_exercise_id"]

    client.post(f"{API}/workout-sessions/{session_id}/finish", headers=owner, json={})
    assert (
        client.post(
            f"{API}/session-exercises/{exercise_id}/sets", headers=owner,
            json={"reps": 5},
        ).status_code
        == 409
    )

    reopened = client.post(
        f"{API}/workout-sessions/{session_id}/reopen", headers=owner
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "in_progress"
    assert reopened.json()["completed_at"] is None

    added = client.post(
        f"{API}/session-exercises/{exercise_id}/sets", headers=owner,
        json={"reps": 5},
    )
    assert added.status_code == 201, added.text


def test_reopening_does_not_count_the_gap_as_training(client: TestClient, owner):
    """The stretch between finishing and picking it back up is not a workout.

    Without banking it, a session finished at noon and reopened at five would
    report a five-hour training session.
    """
    session = _start(client, owner)
    sid = session["session_id"]

    client.post(f"{API}/workout-sessions/{sid}/finish", headers=owner, json={})
    banked_before = client.get(
        f"{API}/workout-sessions/{sid}", headers=owner
    ).json()["paused_seconds"]

    time.sleep(1.2)
    reopened = client.post(f"{API}/workout-sessions/{sid}/reopen", headers=owner)

    assert reopened.json()["paused_seconds"] > banked_before, (
        "the time between finishing and reopening must be excluded"
    )


def test_reopening_is_refused_while_another_workout_is_open(
    client: TestClient, owner
):
    """One workout at a time is a partial unique index, not a preference."""
    first = _start(client, owner)
    client.post(
        f"{API}/workout-sessions/{first['session_id']}/finish", headers=owner, json={}
    )
    second = _start(client, owner)

    clash = client.post(
        f"{API}/workout-sessions/{first['session_id']}/reopen", headers=owner
    )
    assert clash.status_code == 409
    assert str(second["session_id"]) in clash.json()["detail"], (
        "the refusal has to name the session standing in the way"
    )


def test_reopening_an_active_session_is_idempotent(client: TestClient, owner):
    """A double tap on a slow connection must not bank the gap twice."""
    session = _start(client, owner)
    sid = session["session_id"]

    first = client.post(f"{API}/workout-sessions/{sid}/reopen", headers=owner)
    assert first.status_code == 200
    assert first.json()["status"] == "in_progress"
    assert first.json()["paused_seconds"] == 0


# ============================================================
# Keeping a session in step with the plan
# ============================================================


def test_syncing_brings_in_exercises_added_to_the_plan(
    client: TestClient, owner, plan_with_day
):
    """Swapping an exercise in the calendar used to leave the open session
    offering only the old one, with no way out of it."""
    day_id = plan_with_day["day"]["workout_day_id"]
    session = _start(client, owner, day_id)
    assert len(session["exercises"]) == 1

    client.post(
        f"{API}/workout-days/{day_id}/exercises",
        headers=owner,
        json={"exercise_id": 2, "sets": [{"target_reps": 8}]},
    )

    synced = client.post(
        f"{API}/workout-sessions/{session['session_id']}/sync-plan", headers=owner
    )
    assert synced.status_code == 200
    assert [e["exercise_id"] for e in synced.json()["exercises"]] == [1, 2]


def test_syncing_drops_an_exercise_taken_off_the_plan_even_if_logged(
    client: TestClient, owner, plan_with_day
):
    """Taking an exercise off today's plan is the user saying they are not
    doing it. A half-finished orphan left behind contradicts that."""
    day_id = plan_with_day["day"]["workout_day_id"]
    session = _start(client, owner, day_id)
    set_id = session["exercises"][0]["sets"][0]["session_set_id"]

    client.put(
        f"{API}/session-sets/{set_id}",
        headers=owner,
        json={"reps": 10, "weight_kg": 40, "completed": True},
    )

    planned_id = plan_with_day["exercise"]["workout_exercise_id"]
    assert (
        client.delete(f"{API}/workout-exercises/{planned_id}", headers=owner)
        .status_code
        == 204
    )

    synced = client.post(
        f"{API}/workout-sessions/{session['session_id']}/sync-plan", headers=owner
    )
    assert synced.status_code == 200
    assert synced.json()["exercises"] == []


def test_syncing_keeps_logged_sets_for_exercises_still_planned(
    client: TestClient, owner, plan_with_day
):
    """Editing a *different* row is no reason to throw away work already done."""
    day_id = plan_with_day["day"]["workout_day_id"]
    session = _start(client, owner, day_id)
    set_id = session["exercises"][0]["sets"][0]["session_set_id"]

    client.put(
        f"{API}/session-sets/{set_id}",
        headers=owner,
        json={"reps": 10, "weight_kg": 42.5, "completed": True},
    )
    client.post(
        f"{API}/workout-days/{day_id}/exercises",
        headers=owner,
        json={"exercise_id": 2},
    )

    synced = client.post(
        f"{API}/workout-sessions/{session['session_id']}/sync-plan", headers=owner
    ).json()

    kept = next(e for e in synced["exercises"] if e["exercise_id"] == 1)
    logged = next(s for s in kept["sets"] if s["session_set_id"] == set_id)
    assert float(logged["weight_kg"]) == 42.5
    assert logged["completed"] is True


def test_syncing_a_freehand_session_changes_nothing(client: TestClient, owner):
    """A session started without a day has no plan to be in step with."""
    session = _start(client, owner)
    synced = client.post(
        f"{API}/workout-sessions/{session['session_id']}/sync-plan", headers=owner
    )
    assert synced.status_code == 200
    assert synced.json()["session_id"] == session["session_id"]
