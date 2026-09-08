"""Phase 5: what the camera recorded about a set.

The rules pinned here are the ones that were argued: a set's score is a
percentage of what it could have scored (so a 5-rep set and a 20-rep set are
comparable), the AI fields are a snapshot that a correction may not rewrite,
and the badge is counted from the sets themselves - so finishing twice cannot
award it twice.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import API


def _start(client: TestClient, owner, day_id: int | None = None) -> dict:
    body = {"workout_day_id": day_id} if day_id else {}
    response = client.post(f"{API}/workout-sessions", headers=owner, json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _finish(client: TestClient, owner, session_id: int) -> None:
    response = client.post(
        f"{API}/workout-sessions/{session_id}/finish", headers=owner, json={}
    )
    assert response.status_code == 200, response.text


def _log_ai_set(
    client: TestClient, owner, session_exercise_id: int, scores: list[int]
) -> dict:
    """Record a set the way the camera screen does: reps plus per-rep scores."""
    response = client.post(
        f"{API}/session-exercises/{session_exercise_id}/sets",
        headers=owner,
        json={
            "reps": len(scores),
            "weight_kg": 0,
            "completed": True,
            "ai_algorithm": "pushup_v1",
            "ai_rep_scores": scores,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# ============================================================
# Scoring
# ============================================================


def test_a_set_scores_against_what_it_could_have_scored(
    client: TestClient, owner, plan_with_day
):
    """Two deep reps and two half-depth ones is 300 of a possible 400.

    A raw total would just reward doing more reps; the percentage is what makes
    a short set and a long one comparable.
    """
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    exercise_id = session["exercises"][0]["session_exercise_id"]

    performed = _log_ai_set(client, owner, exercise_id, [100, 100, 50, 50])

    assert float(performed["ai_score_percent"]) == 75.0
    assert performed["ai_rep_scores"] == [100, 100, 50, 50]
    assert performed["ai_algorithm"] == "pushup_v1"


def test_a_hand_logged_set_has_no_score(client: TestClient, owner, plan_with_day):
    """No scores is not a bad score - the set simply was not judged."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    response = client.put(
        f"{API}/session-sets/{session['exercises'][0]['sets'][0]['session_set_id']}",
        headers=owner,
        json={"reps": 10, "weight_kg": 40, "completed": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["ai_score_percent"] is None
    assert response.json()["ai_algorithm"] is None


def test_a_rep_score_must_be_a_milestone(client: TestClient, owner, plan_with_day):
    """0, 50 or 100 and nothing else: another number means the client and the
    server disagree about the scoring rule, and accepting it would put a figure
    in the history that nothing can explain."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    response = client.post(
        f"{API}/session-exercises/{session['exercises'][0]['session_exercise_id']}/sets",
        headers=owner,
        json={"reps": 2, "completed": True, "ai_rep_scores": [100, 73]},
    )
    assert response.status_code == 422, response.text


def test_a_correction_cannot_rewrite_how_the_camera_judged_the_set(
    client: TestClient, owner, plan_with_day
):
    """Correcting a mistyped rep count is fair; re-scoring the performance
    afterwards is not, for the same reason target_* is not editable."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    exercise_id = session["exercises"][0]["session_exercise_id"]
    performed = _log_ai_set(client, owner, exercise_id, [100, 50])

    response = client.put(
        f"{API}/session-sets/{performed['session_set_id']}",
        headers=owner,
        json={"reps": 3, "ai_rep_scores": [100, 100, 100], "ai_algorithm": "cheat"},
    )
    assert response.status_code == 200, response.text

    corrected = response.json()
    assert corrected["reps"] == 3, "the rep count is the user's to correct"
    assert corrected["ai_rep_scores"] == [100, 50], "the scores are not"
    assert corrected["ai_algorithm"] == "pushup_v1"


# ============================================================
# The exercise says whether it can be scored at all
# ============================================================


def test_an_exercise_reports_whether_the_camera_can_score_it(
    client: TestClient, owner
):
    """The client decides whether to offer the AI camera from this field, so it
    has to travel with the exercise wherever a set is entered."""
    detail = client.get(f"{API}/exercises/1", headers=owner).json()
    assert "ai_algorithm" in detail
    assert "tracking_type" in detail

    listed = client.get(f"{API}/exercises?limit=1", headers=owner).json()
    assert "ai_algorithm" in listed["items"][0]


def test_a_session_exercise_carries_the_algorithm(
    client: TestClient, owner, plan_with_day
):
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    assert "ai_algorithm" in session["exercises"][0]


# ============================================================
# The badge
# ============================================================


def test_clean_sets_unlock_the_form_badge(client: TestClient, owner, plan_with_day):
    """Ten sets at 90% or better. The ninth must not unlock it and the tenth
    must, because the requirement is a number, not a vibe."""
    day_id = plan_with_day["day"]["workout_day_id"]

    def badge() -> dict:
        catalog = client.get(f"{API}/achievements", headers=owner).json()
        return {a["code"]: a for a in catalog}["FORM_MASTER"]

    for logged in range(10):
        session = _start(client, owner, day_id)
        exercise_id = session["exercises"][0]["session_exercise_id"]
        # 9 of 10 reps deep = 95%, comfortably over the 90% bar.
        _log_ai_set(client, owner, exercise_id, [100] * 9 + [50])
        _finish(client, owner, session["session_id"])

        if logged == 8:
            assert badge()["unlocked"] is False
            assert badge()["current_value"] == 9.0

    assert badge()["unlocked"] is True


def test_a_sloppy_set_does_not_count_towards_the_badge(
    client: TestClient, owner, plan_with_day
):
    """Half the reps at half depth is 75%, which is a set that happened and a
    set that was not clean."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    _log_ai_set(
        client, owner, session["exercises"][0]["session_exercise_id"],
        [100, 100, 50, 50],
    )
    _finish(client, owner, session["session_id"])

    catalog = {
        a["code"]: a
        for a in client.get(f"{API}/achievements", headers=owner).json()
    }
    assert catalog["FORM_MASTER"]["current_value"] == 0.0


def test_refinishing_does_not_inflate_the_clean_set_count(
    client: TestClient, owner, plan_with_day
):
    """The count is a query over the sets, not a counter - so reopening and
    finishing again still means one clean set."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    session_id = session["session_id"]
    _log_ai_set(
        client, owner, session["exercises"][0]["session_exercise_id"], [100] * 5
    )
    _finish(client, owner, session_id)

    for _ in range(3):
        client.post(f"{API}/workout-sessions/{session_id}/reopen", headers=owner)
        _finish(client, owner, session_id)

    catalog = {
        a["code"]: a
        for a in client.get(f"{API}/achievements", headers=owner).json()
    }
    assert catalog["FORM_MASTER"]["current_value"] == 1.0


def test_an_ai_set_sets_no_form_personal_record(
    client: TestClient, owner, plan_with_day
):
    """Form quality is deliberately not a personal record: a record is a
    ceiling on load, and mixing a percentage into "my PRs" would list 95% next
    to 50 kg."""
    session = _start(client, owner, plan_with_day["day"]["workout_day_id"])
    _log_ai_set(
        client, owner, session["exercises"][0]["session_exercise_id"], [100] * 5
    )
    _finish(client, owner, session["session_id"])

    records = client.get(f"{API}/personal-records", headers=owner).json()
    assert all("form" not in r["record_type"] for r in records)
