"""Phase 2: plans, days, exercises, target sets, schedules.

Every test here pins a decision that was reached by argument or measurement,
not a coincidence of the current code.
"""

from __future__ import annotations

import datetime

from fastapi.testclient import TestClient

from tests.conftest import API


# ============================================================
# Ordering
# ============================================================


def test_reorder_needs_one_transaction_not_a_put_per_row(
    client: TestClient, owner, plan_with_day
):
    """Why reordering is a bulk endpoint.

    The `*_order` unique constraints are DEFERRABLE INITIALLY DEFERRED, so a
    duplicate position is tolerated *within* a transaction. Two separate
    requests are two transactions, which is exactly why the single-row PUT
    refuses to accept an order at all.
    """
    day_id = plan_with_day["day"]["workout_day_id"]
    second = client.post(
        f"{API}/workout-days/{day_id}/exercises",
        headers=owner,
        json={"exercise_id": 2},
    ).json()
    first = plan_with_day["exercise"]

    # The single-row PUT must not offer a way to set order.
    response = client.put(
        f"{API}/workout-exercises/{first['workout_exercise_id']}",
        headers=owner,
        json={"exercise_order": 2},
    )
    assert response.status_code == 200
    assert response.json()["exercise_order"] == 1, "PUT silently accepted an order"

    # The bulk endpoint swaps them in one go.
    swapped = client.put(
        f"{API}/workout-days/{day_id}/exercises/reorder",
        headers=owner,
        json={
            "items": [
                {
                    "workout_exercise_id": second["workout_exercise_id"],
                    "exercise_order": 1,
                },
                {
                    "workout_exercise_id": first["workout_exercise_id"],
                    "exercise_order": 2,
                },
            ]
        },
    )
    assert swapped.status_code == 200
    assert [e["workout_exercise_id"] for e in swapped.json()["exercises"]] == [
        second["workout_exercise_id"],
        first["workout_exercise_id"],
    ]


def test_a_partial_reorder_is_refused(client: TestClient, owner, plan_with_day):
    """Omitted rows would keep positions that collide with the moved ones, and
    there is no correct way to guess where they belong."""
    day_id = plan_with_day["day"]["workout_day_id"]
    client.post(
        f"{API}/workout-days/{day_id}/exercises",
        headers=owner,
        json={"exercise_id": 2},
    )

    response = client.put(
        f"{API}/workout-days/{day_id}/exercises/reorder",
        headers=owner,
        json={
            "items": [
                {
                    "workout_exercise_id": plan_with_day["exercise"][
                        "workout_exercise_id"
                    ],
                    "exercise_order": 1,
                }
            ]
        },
    )
    assert response.status_code == 422


def test_order_is_appended_when_omitted(client: TestClient, owner, plan_with_day):
    plan_id = plan_with_day["plan"]["plan_id"]
    orders = [
        client.post(
            f"{API}/workout-plans/{plan_id}/days",
            headers=owner,
            json={"name": name},
        ).json()["day_order"]
        for name in ("Pull", "Legs")
    ]
    # The fixture's day is 1.
    assert orders == [2, 3]


# ============================================================
# Ownership
# ============================================================


def test_another_users_rows_answer_404_never_403(
    client: TestClient, owner, stranger, plan_with_day
):
    """403 would confirm the id exists, which is itself a leak. Ownership is
    folded into the WHERE clause so the two cases are indistinguishable."""
    plan_id = plan_with_day["plan"]["plan_id"]
    day_id = plan_with_day["day"]["workout_day_id"]
    exercise_id = plan_with_day["exercise"]["workout_exercise_id"]
    set_id = plan_with_day["exercise"]["sets"][0]["workout_exercise_set_id"]

    checks = [
        client.get(f"{API}/workout-plans/{plan_id}", headers=stranger),
        client.put(f"{API}/workout-days/{day_id}", headers=stranger, json={"name": "x"}),
        client.delete(f"{API}/workout-exercises/{exercise_id}", headers=stranger),
        client.put(
            f"{API}/workout-exercise-sets/{set_id}",
            headers=stranger,
            json={"target_reps": 1},
        ),
        # schedules carry their own user_id with nothing tying it to the plan,
        # so this one can only be caught in the API.
        client.post(
            f"{API}/schedules",
            headers=stranger,
            json={"workout_day_id": day_id, "day_of_week": 1},
        ),
    ]
    assert [c.status_code for c in checks] == [404] * len(checks)


# ============================================================
# Rest days
# ============================================================


def test_a_rest_day_refuses_exercises(client: TestClient, owner, plan_with_day):
    rest = client.post(
        f"{API}/workout-plans/{plan_with_day['plan']['plan_id']}/days",
        headers=owner,
        json={"name": "Rest", "is_rest_day": True},
    ).json()

    response = client.post(
        f"{API}/workout-days/{rest['workout_day_id']}/exercises",
        headers=owner,
        json={"exercise_id": 1},
    )
    assert response.status_code == 409


def test_a_populated_day_cannot_become_a_rest_day(
    client: TestClient, owner, plan_with_day
):
    """Flipping the flag would strand exercises on a day meant to have none -
    a state nothing else in the API can produce."""
    response = client.put(
        f"{API}/workout-days/{plan_with_day['day']['workout_day_id']}",
        headers=owner,
        json={"is_rest_day": True},
    )
    assert response.status_code == 409


# ============================================================
# Schedules and the calendar
# ============================================================


def test_a_schedule_is_either_a_date_or_a_weekday_never_both(
    client: TestClient, owner, plan_with_day
):
    day_id = plan_with_day["day"]["workout_day_id"]

    both = client.post(
        f"{API}/schedules",
        headers=owner,
        json={
            "workout_day_id": day_id,
            "day_of_week": 1,
            "scheduled_date": "2026-09-07",
        },
    )
    assert both.status_code == 422

    neither = client.post(
        f"{API}/schedules", headers=owner, json={"workout_day_id": day_id}
    )
    assert neither.status_code == 422


def test_a_weekly_rule_expands_into_real_dates(client: TestClient, owner, plan_with_day):
    """`day_of_week=1` is a rule, not a date. The calendar endpoint is what
    turns it into actual Mondays; the client must never do that itself."""
    day_id = plan_with_day["day"]["workout_day_id"]
    client.post(
        f"{API}/schedules",
        headers=owner,
        json={"workout_day_id": day_id, "day_of_week": 1},
    )

    calendar = client.get(
        f"{API}/schedules/calendar?from=2026-09-01&to=2026-09-30", headers=owner
    ).json()

    dates = [item["date"] for item in calendar["items"]]
    assert dates == ["2026-09-07", "2026-09-14", "2026-09-21", "2026-09-28"]
    assert all(
        datetime.date.fromisoformat(d).isoweekday() == 1 for d in dates
    )


def test_scheduling_the_same_day_twice_is_refused(
    client: TestClient, owner, plan_with_day
):
    day_id = plan_with_day["day"]["workout_day_id"]
    body = {"workout_day_id": day_id, "scheduled_date": "2026-09-07"}
    assert client.post(f"{API}/schedules", headers=owner, json=body).status_code == 201
    assert client.post(f"{API}/schedules", headers=owner, json=body).status_code == 409


def test_archiving_hides_a_plan_from_the_calendar_but_keeps_its_schedules(
    client: TestClient, owner, plan_with_day
):
    plan_id = plan_with_day["plan"]["plan_id"]
    client.post(
        f"{API}/schedules",
        headers=owner,
        json={
            "workout_day_id": plan_with_day["day"]["workout_day_id"],
            "day_of_week": 1,
        },
    )

    client.put(f"{API}/workout-plans/{plan_id}", headers=owner, json={"status": "archived"})

    calendar = client.get(
        f"{API}/schedules/calendar?from=2026-09-01&to=2026-09-30", headers=owner
    ).json()
    assert calendar["items"] == []
    # Archiving is reversible; the configuration is still there.
    assert client.get(f"{API}/schedules", headers=owner).json()["total"] == 1


# ============================================================
# Naming and the catalog
# ============================================================


def test_exercise_names_come_from_translations_and_follow_language(
    client: TestClient, owner, plan_with_day
):
    """`exercises` has no name column at all; without the translation join
    every planned exercise would be nameless."""
    plan_id = plan_with_day["plan"]["plan_id"]

    vi = client.get(f"{API}/workout-plans/{plan_id}?language=vi", headers=owner).json()
    en = client.get(f"{API}/workout-plans/{plan_id}?language=en", headers=owner).json()

    vi_name = vi["days"][0]["exercises"][0]["name"]
    en_name = en["days"][0]["exercises"][0]["name"]
    assert vi_name and en_name
    assert vi_name != en_name


def test_the_plan_list_stops_above_the_exercises(client: TestClient, owner, plan_with_day):
    """Nesting the whole tree here would load every set of every plan to draw
    a screen of plan names."""
    item = client.get(f"{API}/workout-plans", headers=owner).json()["items"][0]
    assert item["days"], "the list should still preview the days"
    assert "exercises" not in item["days"][0]
    assert item["days"][0]["exercise_count"] == 1


def test_deleting_a_plan_cascades_to_everything_under_it(
    client: TestClient, owner, plan_with_day
):
    plan_id = plan_with_day["plan"]["plan_id"]
    set_id = plan_with_day["exercise"]["sets"][0]["workout_exercise_set_id"]

    assert client.delete(f"{API}/workout-plans/{plan_id}", headers=owner).status_code == 204
    assert client.get(f"{API}/workout-plans/{plan_id}", headers=owner).status_code == 404
    assert (
        client.put(
            f"{API}/workout-exercise-sets/{set_id}", headers=owner, json={"target_reps": 5}
        ).status_code
        == 404
    )


# ============================================================
# Rest days by date
# ============================================================


def test_ticking_rest_on_an_empty_date_creates_the_day(client: TestClient, owner):
    """A rest day is a row, not an absence - only a row can tell the calendar
    "resting today" apart from "nothing scheduled yet"."""
    plan = client.post(
        f"{API}/workout-plans", headers=owner, json={"name": "Rest Test"}
    ).json()
    plan_id = plan["plan_id"]

    assert (
        client.get(f"{API}/workout-plans/{plan_id}/dates/2026-09-10", headers=owner).json()
        is None
    )

    marked = client.put(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-10/rest",
        headers=owner,
        json={"is_rest_day": True},
    )
    assert marked.status_code == 200
    assert marked.json()["is_rest_day"] is True

    # It is now visible by date, and on the calendar.
    fetched = client.get(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-10", headers=owner
    ).json()
    assert fetched["is_rest_day"] is True

    calendar = client.get(
        f"{API}/schedules/calendar?from=2026-09-01&to=2026-09-30", headers=owner
    ).json()
    entry = [i for i in calendar["items"] if i["date"] == "2026-09-10"]
    assert len(entry) == 1 and entry[0]["is_rest_day"] is True


def test_unticking_rest_removes_the_date_again(client: TestClient, owner):
    """A day with no exercises and no rest flag means nothing, and would show
    on the calendar as an empty workout."""
    plan = client.post(
        f"{API}/workout-plans", headers=owner, json={"name": "Rest Test"}
    ).json()
    plan_id = plan["plan_id"]

    client.put(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-11/rest",
        headers=owner,
        json={"is_rest_day": True},
    )
    cleared = client.put(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-11/rest",
        headers=owner,
        json={"is_rest_day": False},
    )
    assert cleared.status_code == 200
    assert cleared.json() is None
    assert (
        client.get(f"{API}/workout-plans/{plan_id}/dates/2026-09-11", headers=owner).json()
        is None
    )


def test_a_date_with_exercises_cannot_be_marked_as_rest(client: TestClient, owner):
    plan = client.post(
        f"{API}/workout-plans", headers=owner, json={"name": "Rest Test"}
    ).json()
    plan_id = plan["plan_id"]
    client.put(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-12/exercises",
        headers=owner,
        json={"exercise_ids": [1]},
    )

    response = client.put(
        f"{API}/workout-plans/{plan_id}/dates/2026-09-12/rest",
        headers=owner,
        json={"is_rest_day": True},
    )
    assert response.status_code == 409


def test_rest_day_on_someone_elses_plan_is_404(client: TestClient, stranger, plan_with_day):
    response = client.put(
        f"{API}/workout-plans/{plan_with_day['plan']['plan_id']}/dates/2026-09-13/rest",
        headers=stranger,
        json={"is_rest_day": True},
    )
    assert response.status_code == 404
