"""The food diary.

Every test here pins a decision that was argued, not a coincidence of the
current code: why a per-100 g row refuses to be logged in servings, why
correcting the catalog must not move yesterday's total, why a planned food
does not count toward today's total.

Foods are looked up from the real catalog rather than hardcoded by id. The
seed is regenerated from viendinhduong.vn and the ids move; a test that fails
because the seeder ran again is a test nobody trusts.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.conftest import API

TODAY = date(2026, 3, 2)
YESTERDAY = TODAY - timedelta(days=1)


# --------------------------------------------------------------------------
# Catalog rows the diary is logged against
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def catalog():
    """One row of each shape the diary has to handle.

    Resolved once per module: these are reads of seeded data that no test
    mutates, and re-querying them per test adds a round trip to the hosted
    database for nothing.
    """
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        dish = db.execute(text("""
            SELECT f.food_id, f.energy_kcal, f.serving_grams
            FROM foods f
            WHERE f.basis = 'per_serving' AND f.serving_grams IS NOT NULL
              AND f.protein_g IS NOT NULL
            ORDER BY f.food_id LIMIT 1
        """)).one()

        weightless = db.execute(text("""
            SELECT f.food_id, f.energy_kcal
            FROM foods f
            WHERE f.basis = 'per_serving' AND f.serving_grams IS NULL
            ORDER BY f.food_id LIMIT 1
        """)).one()

        ingredient = db.execute(text("""
            SELECT f.food_id, f.energy_kcal
            FROM foods f
            WHERE f.basis = 'per_100g' AND f.energy_kcal > 0
            ORDER BY f.food_id LIMIT 1
        """)).one()

    return {
        "dish": {"id": dish[0], "kcal": Decimal(dish[1]), "grams": Decimal(dish[2])},
        "weightless": {"id": weightless[0], "kcal": Decimal(weightless[1])},
        "ingredient": {"id": ingredient[0], "kcal": Decimal(ingredient[1])},
    }


def _meal(client, headers, day=TODAY, name=None):
    """Create a meal and return its id."""
    response = client.post(
        f"{API}/meals",
        headers=headers,
        json={"logged_date": day.isoformat(), "name": name},
    )
    assert response.status_code == 201, response.text
    return response.json()["meal_id"]


def _log(
    client,
    headers,
    food_id,
    quantity,
    unit,
    meal_id=None,
    day=TODAY,
    status="eaten",
):
    """Log (or plan) a food. A meal is created on `day` unless one is given -
    most tests care about the food, not about which meal it sits in."""
    if meal_id is None:
        meal_id = _meal(client, headers, day)
    return client.post(
        f"{API}/meal-items",
        headers=headers,
        json={
            "meal_id": meal_id,
            "food_id": food_id,
            "quantity": str(quantity),
            "unit": unit,
            "status": status,
        },
    )


def _day(client, headers, day=TODAY):
    return client.get(
        f"{API}/meals", headers=headers, params={"date": day.isoformat()}
    ).json()


# --------------------------------------------------------------------------
# Scaling: the whole point of `basis`
# --------------------------------------------------------------------------


def test_a_dish_is_logged_in_servings_and_scales(client, owner, catalog):
    """1.5 servings is 1.5x the published figures, and 1.5x the weight."""
    dish = catalog["dish"]
    response = _log(client, owner, dish["id"], "1.5", "serving")
    assert response.status_code == 201, response.text

    item = response.json()
    assert Decimal(item["energy_kcal"]) == (dish["kcal"] * Decimal("1.5")).quantize(
        Decimal("0.01")
    )
    assert Decimal(item["grams"]) == (dish["grams"] * Decimal("1.5")).quantize(
        Decimal("0.1")
    )


def test_an_ingredient_is_logged_in_grams_and_scales_per_hundred(
    client, owner, catalog
):
    """250 g of a per-100 g row is 2.5x what the table publishes."""
    ingredient = catalog["ingredient"]
    response = _log(client, owner, ingredient["id"], "250", "gram")
    assert response.status_code == 201, response.text

    item = response.json()
    expected = (ingredient["kcal"] * Decimal("2.5")).quantize(Decimal("0.01"))
    assert Decimal(item["energy_kcal"]) == expected
    assert Decimal(item["grams"]) == Decimal("250.0")


def test_an_ingredient_refuses_to_be_logged_in_servings(client, owner, catalog):
    """Nobody published what one serving of a raw ingredient is.

    Accepting this and quietly assuming 100 g is how a food app misreports
    intake. This is the gap `food_portions` was designed for and has no data
    for yet, so the honest answer is a refusal that says which unit to use.
    """
    response = _log(client, owner, catalog["ingredient"]["id"], "1", "serving")
    assert response.status_code == 422
    assert "grams" in response.json()["detail"]


def test_a_dish_with_no_published_weight_refuses_grams(client, owner, catalog):
    """237 dishes have no serving weight, so grams cannot be converted."""
    response = _log(client, owner, catalog["weightless"]["id"], "100", "gram")
    assert response.status_code == 422
    assert "servings" in response.json()["detail"]


def test_a_dish_with_no_published_weight_still_logs_in_servings(
    client, owner, catalog
):
    """An unknown weight does not make the entry unloggable - "1 suất phở" is
    a complete fact. `grams` comes back null rather than guessed."""
    response = _log(client, owner, catalog["weightless"]["id"], "1", "serving")
    assert response.status_code == 201, response.text
    assert response.json()["grams"] is None


# --------------------------------------------------------------------------
# The snapshot rule
# --------------------------------------------------------------------------


def test_correcting_the_catalog_does_not_rewrite_what_was_logged(
    client, owner, catalog, db_session
):
    """The diary is a record, not a live query.

    Somebody who hit their 2000 kcal target on Tuesday must not become over
    budget because the catalog was corrected on Friday.
    """
    dish = catalog["dish"]
    item = _log(client, owner, dish["id"], "1", "serving", day=YESTERDAY).json()
    logged_kcal = Decimal(item["energy_kcal"])

    original = db_session.execute(
        text("SELECT energy_kcal FROM foods WHERE food_id = :i"), {"i": dish["id"]}
    ).scalar_one()
    try:
        db_session.execute(
            text("UPDATE foods SET energy_kcal = energy_kcal + 500 WHERE food_id = :i"),
            {"i": dish["id"]},
        )
        db_session.commit()

        day = client.get(
            f"{API}/meals", headers=owner, params={"date": YESTERDAY.isoformat()}
        ).json()
        assert Decimal(day["totals"]["energy_kcal"]) == logged_kcal
    finally:
        db_session.execute(
            text("UPDATE foods SET energy_kcal = :v WHERE food_id = :i"),
            {"v": original, "i": dish["id"]},
        )
        db_session.commit()


# --------------------------------------------------------------------------
# Shape of a day
# --------------------------------------------------------------------------


def test_foods_added_to_one_meal_share_it(client, owner, catalog):
    """A meal is a container the user made; adding a second food to it appends
    an item rather than creating another meal."""
    meal_id = _meal(client, owner, name="Bữa trưa")
    _log(client, owner, catalog["dish"]["id"], "1", "serving", meal_id=meal_id)
    _log(client, owner, catalog["ingredient"]["id"], "100", "gram", meal_id=meal_id)

    meals = _day(client, owner)["meals"]
    assert len(meals) == 1
    assert len(meals[0]["items"]) == 2


def test_a_day_totals_its_items_and_subtracts_the_target(client, owner, catalog):
    client.put(
        f"{API}/nutrition-targets", headers=owner, json={"energy_kcal": "2000"}
    )
    _log(client, owner, catalog["dish"]["id"], "1", "serving")

    day = _day(client, owner)

    eaten = Decimal(day["totals"]["energy_kcal"])
    assert eaten == sum(
        Decimal(i["energy_kcal"]) for m in day["meals"] for i in m["items"]
    )
    assert Decimal(day["remaining_kcal"]) == Decimal("2000") - eaten


def test_a_day_without_a_target_reports_no_remaining(client, owner, catalog):
    """None, not zero: "you have none left" and "you never set one" are
    different answers and the screen shows them differently."""
    _log(client, owner, catalog["dish"]["id"], "1", "serving")

    day = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()
    assert day["remaining_kcal"] is None
    assert day["target"] is None


def test_an_untouched_day_comes_back_empty(client, owner):
    day = client.get(
        f"{API}/meals", headers=owner, params={"date": "2020-01-01"}
    ).json()
    assert day["meals"] == []
    assert Decimal(day["totals"]["energy_kcal"]) == 0


# --------------------------------------------------------------------------
# Editing
# --------------------------------------------------------------------------


def test_correcting_an_amount_recomputes_the_nutrition(client, owner, catalog):
    item = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()
    single = Decimal(item["energy_kcal"])

    updated = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "2", "unit": "serving"},
    )
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["energy_kcal"]) == (single * 2).quantize(
        Decimal("0.01")
    )


def test_a_meal_survives_losing_its_last_food(client, owner, catalog):
    """The user created the meal, so an empty one is where the next food goes.
    (It used to vanish, back when meals were made as a side effect of logging
    a food.)"""
    meal_id = _meal(client, owner, name="Bữa sáng")
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", meal_id=meal_id
    ).json()

    assert client.delete(
        f"{API}/meal-items/{item['meal_item_id']}", headers=owner
    ).status_code == 204

    kept = [m for m in _day(client, owner)["meals"] if m["meal_id"] == meal_id]
    assert len(kept) == 1
    assert kept[0]["items"] == []
    assert kept[0]["name"] == "Bữa sáng"


def test_a_note_can_be_set_and_cleared(client, owner):
    meal_id = _meal(client, owner)

    client.put(
        f"{API}/meals/{meal_id}", headers=owner, json={"note": "ăn ở nhà bà ngoại"}
    )
    meal = _day(client, owner)["meals"][0]
    assert meal["note"] == "ăn ở nhà bà ngoại"

    client.put(f"{API}/meals/{meal_id}", headers=owner, json={"note": None})
    assert _day(client, owner)["meals"][0]["note"] is None


# --------------------------------------------------------------------------
# Meals named by the user
# --------------------------------------------------------------------------


def test_a_day_can_hold_more_than_four_meals_in_the_order_they_were_added(
    client, owner
):
    """The four fixed meals were the limit this replaced: somebody who eats
    five small meals could not have five."""
    names = ["Sáng", "Phụ sáng", "Trưa", "Xế", "Tối", "Trước ngủ"]
    for name in names:
        _meal(client, owner, name=name)

    meals = _day(client, owner)["meals"]
    assert [m["name"] for m in meals] == names
    assert [m["position"] for m in meals] == list(range(len(names)))


def test_two_meals_may_share_a_name(client, owner):
    """Nothing makes a name unique - two snacks are two snacks."""
    _meal(client, owner, name="Ăn vặt")
    _meal(client, owner, name="Ăn vặt")
    assert len(_day(client, owner)["meals"]) == 2


def test_a_meal_without_a_name_has_none_stored(client, owner):
    """The "Bữa 1" placeholder is the app's to draw. Storing it would put words
    in the user's mouth, and would not survive a language change."""
    meal_id = _meal(client, owner)
    meal = _day(client, owner)["meals"][0]
    assert meal["meal_id"] == meal_id
    assert meal["name"] is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_name_is_no_name(client, owner, blank):
    _meal(client, owner, name=blank)
    assert _day(client, owner)["meals"][0]["name"] is None


def test_a_meal_can_be_renamed_and_the_name_cleared(client, owner):
    meal_id = _meal(client, owner, name="Bữa 1")

    client.put(f"{API}/meals/{meal_id}", headers=owner, json={"name": "  Sau tập  "})
    assert _day(client, owner)["meals"][0]["name"] == "Sau tập"

    # Omitting the name keeps it; sending null clears it back to the placeholder.
    client.put(f"{API}/meals/{meal_id}", headers=owner, json={"note": "x"})
    assert _day(client, owner)["meals"][0]["name"] == "Sau tập"

    client.put(f"{API}/meals/{meal_id}", headers=owner, json={"name": None})
    assert _day(client, owner)["meals"][0]["name"] is None


def test_a_name_too_long_is_rejected(client, owner):
    response = client.post(
        f"{API}/meals",
        headers=owner,
        json={"logged_date": TODAY.isoformat(), "name": "x" * 61},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Planned and eaten
# --------------------------------------------------------------------------


def test_a_planned_food_is_not_counted_as_eaten(client, owner, catalog):
    """Totals and the target comparison are about what happened. Counting a
    plan would tell somebody they had already hit their target at breakfast."""
    client.put(
        f"{API}/nutrition-targets", headers=owner, json={"energy_kcal": "2000"}
    )
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()

    day = _day(client, owner)
    assert Decimal(day["totals"]["energy_kcal"]) == 0
    assert Decimal(day["remaining_kcal"]) == Decimal("2000")
    # ...but the plan has a number of its own.
    assert Decimal(day["planned_kcal"]) == Decimal(item["energy_kcal"])
    assert item["status"] == "planned"


def test_a_future_day_can_be_planned_and_read_back(client, owner, catalog):
    later = TODAY + timedelta(days=30)
    _log(
        client, owner, catalog["dish"]["id"], "2", "serving", day=later,
        status="planned",
    )

    day = _day(client, owner, later)
    assert len(day["meals"]) == 1
    assert day["meals"][0]["items"][0]["status"] == "planned"
    assert Decimal(day["planned_kcal"]) > 0


def test_ticking_a_planned_food_counts_it_as_eaten(client, owner, catalog):
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()

    ticked = client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "eaten"},
    )
    assert ticked.status_code == 200, ticked.text
    assert ticked.json()["status"] == "eaten"

    day = _day(client, owner)
    assert Decimal(day["totals"]["energy_kcal"]) == Decimal(item["energy_kcal"])


def test_ticking_with_a_different_amount_keeps_the_plan_on_record(
    client, owner, catalog
):
    """"Planned 2 bowls, ate 1" has to stay answerable - which is why the plan
    is kept beside the actual instead of being overwritten by it."""
    item = _log(
        client, owner, catalog["dish"]["id"], "2", "serving", status="planned"
    ).json()
    planned_kcal = Decimal(item["energy_kcal"])

    ticked = client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "eaten", "quantity": "1", "unit": "serving"},
    ).json()

    assert Decimal(ticked["quantity"]) == 1
    assert Decimal(ticked["energy_kcal"]) < planned_kcal
    assert Decimal(ticked["planned_quantity"]) == 2
    assert Decimal(ticked["planned_energy_kcal"]) == planned_kcal

    day = _day(client, owner)
    assert Decimal(day["totals"]["energy_kcal"]) == Decimal(ticked["energy_kcal"])
    assert Decimal(day["planned_kcal"]) == planned_kcal


def test_putting_an_eaten_food_back_restores_the_planned_amount(
    client, owner, catalog
):
    """An accidental tap should be undoable without retyping anything."""
    item = _log(
        client, owner, catalog["dish"]["id"], "2", "serving", status="planned"
    ).json()
    url = f"{API}/meal-items/{item['meal_item_id']}/status"
    client.put(
        url, headers=owner,
        json={"status": "eaten", "quantity": "1", "unit": "serving"},
    )

    back = client.put(url, headers=owner, json={"status": "planned"}).json()
    assert back["status"] == "planned"
    assert Decimal(back["quantity"]) == 2
    assert Decimal(back["energy_kcal"]) == Decimal(item["energy_kcal"])
    assert Decimal(_day(client, owner)["totals"]["energy_kcal"]) == 0


def test_something_never_planned_cannot_be_put_back(client, owner, catalog):
    item = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()
    assert item["planned_quantity"] is None

    response = client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "planned"},
    )
    assert response.status_code == 409


def test_ticking_twice_changes_nothing(client, owner, catalog):
    """A double tap must not be a failure, and must not move any figure."""
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()
    url = f"{API}/meal-items/{item['meal_item_id']}/status"

    first = client.put(url, headers=owner, json={"status": "eaten"}).json()
    second = client.put(url, headers=owner, json={"status": "eaten"})
    assert second.status_code == 200
    assert second.json()["energy_kcal"] == first["energy_kcal"]


def test_a_quantity_without_a_unit_is_rejected(client, owner, catalog):
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()
    response = client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "eaten", "quantity": "1"},
    )
    assert response.status_code == 422


def test_editing_a_planned_food_revises_the_plan(client, owner, catalog):
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()

    edited = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "3", "unit": "serving"},
    ).json()

    assert Decimal(edited["quantity"]) == 3
    assert Decimal(edited["planned_quantity"]) == 3
    assert Decimal(edited["planned_energy_kcal"]) == Decimal(edited["energy_kcal"])


def test_editing_an_eaten_food_leaves_the_plan_alone(client, owner, catalog):
    """Correcting what was eaten is not revising what was planned."""
    item = _log(
        client, owner, catalog["dish"]["id"], "2", "serving", status="planned"
    ).json()
    client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "eaten"},
    )

    edited = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "1", "unit": "serving"},
    ).json()

    assert Decimal(edited["quantity"]) == 1
    assert Decimal(edited["planned_quantity"]) == 2


def test_the_history_counts_only_what_was_eaten(client, owner, catalog):
    """A week of planning must not fill the history with meals that have not
    happened yet."""
    _log(client, owner, catalog["dish"]["id"], "1", "serving", status="planned")

    rows = client.get(
        f"{API}/nutrition/summary",
        headers=owner,
        params={"from": TODAY.isoformat(), "to": TODAY.isoformat()},
    ).json()
    assert rows == []


# --------------------------------------------------------------------------
# The month calendar
# --------------------------------------------------------------------------


def _calendar(client, headers, start, end):
    return client.get(
        f"{API}/meals/calendar",
        headers=headers,
        params={"from": start.isoformat(), "to": end.isoformat()},
    )


def test_the_calendar_shows_eaten_and_planned_side_by_side(client, owner, catalog):
    """Unlike the history, the calendar must show a planned day - which future
    dates already have meals lined up is the point of opening it."""
    later = TODAY + timedelta(days=3)
    eaten = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()
    planned = _log(
        client, owner, catalog["dish"]["id"], "2", "serving", day=later,
        status="planned",
    ).json()

    days = {d["logged_date"]: d for d in _calendar(client, owner, TODAY, later).json()}

    assert Decimal(days[TODAY.isoformat()]["eaten_kcal"]) == Decimal(eaten["energy_kcal"])
    assert days[TODAY.isoformat()]["eaten_count"] == 1
    assert Decimal(days[later.isoformat()]["eaten_kcal"]) == 0
    assert Decimal(days[later.isoformat()]["planned_kcal"]) == Decimal(
        planned["planned_energy_kcal"]
    )
    assert days[later.isoformat()]["planned_count"] == 1
    # A date with nothing on it is absent, not zero.
    assert (TODAY + timedelta(days=1)).isoformat() not in days


def test_the_calendar_is_the_callers_own(client, owner, stranger, catalog):
    _log(client, owner, catalog["dish"]["id"], "1", "serving")
    assert _calendar(client, stranger, TODAY, TODAY).json() == []


@pytest.mark.parametrize(
    "start,end",
    [(TODAY, TODAY - timedelta(days=1)), (TODAY, TODAY + timedelta(days=90))],
)
def test_the_calendar_refuses_a_backwards_or_huge_range(client, owner, start, end):
    assert _calendar(client, owner, start, end).status_code == 422


# --------------------------------------------------------------------------
# Typed in by hand
# --------------------------------------------------------------------------


def _custom(client, headers, meal_id=None, **body):
    if meal_id is None:
        meal_id = _meal(client, headers)
    payload = {"meal_id": meal_id, "name": "Bánh mì mẹ làm", "energy_kcal": "350"}
    payload.update(body)
    return client.post(f"{API}/meal-items/custom", headers=headers, json=payload)


def test_a_food_not_in_the_catalog_can_be_typed_in(client, owner):
    """Figures are typed per serving and multiplied out, so the row holds what
    was eaten - the same shape as a catalog entry."""
    response = _custom(
        client, owner, quantity="2", protein_g="12", fat_g="10.5"
    )
    assert response.status_code == 201, response.text
    item = response.json()

    assert item["is_custom"] is True
    assert item["food_id"] is None
    assert item["unit"] == "serving"
    assert item["grams"] is None
    assert Decimal(item["energy_kcal"]) == Decimal("700")
    assert Decimal(item["protein_g"]) == Decimal("24")
    assert Decimal(item["fat_g"]) == Decimal("21")
    # A macro nobody typed stays unknown rather than becoming zero.
    assert item["carb_g"] is None

    assert Decimal(_day(client, owner)["totals"]["energy_kcal"]) == Decimal("700")


def test_a_typed_in_food_counts_toward_the_day_only_once_eaten(client, owner):
    item = _custom(client, owner, status="planned").json()
    assert Decimal(_day(client, owner)["totals"]["energy_kcal"]) == 0

    client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=owner,
        json={"status": "eaten"},
    )
    assert Decimal(_day(client, owner)["totals"]["energy_kcal"]) == Decimal("350")


def test_a_typed_in_food_can_be_rescaled_without_a_catalog_row(client, owner):
    """Its figures were entered per serving, so changing the amount scales them
    exactly - unlike a catalog entry whose food was removed, which refuses."""
    item = _custom(client, owner, protein_g="10").json()

    edited = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "1.5", "unit": "serving"},
    )
    assert edited.status_code == 200, edited.text
    assert Decimal(edited.json()["energy_kcal"]) == Decimal("525")
    assert Decimal(edited.json()["protein_g"]) == Decimal("15")


def test_a_typed_in_food_refuses_grams(client, owner):
    item = _custom(client, owner).json()
    response = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "100", "unit": "gram"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [{"name": "   "}, {"energy_kcal": "-1"}, {"quantity": "0"}, {"name": "x" * 201}],
)
def test_an_impossible_typed_in_food_is_rejected(client, owner, body):
    assert _custom(client, owner, **body).status_code == 422


def test_a_typed_in_food_cannot_go_into_someone_elses_meal(client, owner, stranger):
    meal_id = _meal(client, owner)
    assert _custom(client, stranger, meal_id=meal_id).status_code == 404


# --------------------------------------------------------------------------
# Recently eaten
# --------------------------------------------------------------------------


def _recent(client, headers, **params):
    response = client.get(f"{API}/meal-items/recent", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_recent_foods_are_newest_first_and_each_appears_once(client, owner, catalog):
    """The picker shows these before anything is typed. Somebody who has phở
    every morning should see it once, at the top, not seven times."""
    _log(client, owner, catalog["dish"]["id"], "1", "serving")
    _log(client, owner, catalog["ingredient"]["id"], "150", "gram")
    _log(client, owner, catalog["dish"]["id"], "2", "serving")

    recent = _recent(client, owner)
    ids = [r["food"]["food_id"] for r in recent if r["kind"] == "catalog"]
    assert ids == [catalog["dish"]["id"], catalog["ingredient"]["id"]]
    # The amount last eaten is the likeliest amount now.
    assert Decimal(recent[0]["last_quantity"]) == 2


def test_recent_foods_include_typed_in_ones_per_serving(client, owner):
    """Tapping a hand-typed recent fills the form in again, so its figures come
    back per serving whatever amount was eaten last time."""
    _custom(client, owner, quantity="2", energy_kcal="300", protein_g="8")

    recent = _recent(client, owner)
    custom = next(r for r in recent if r["kind"] == "custom")
    assert custom["custom"]["name"] == "Bánh mì mẹ làm"
    assert Decimal(custom["custom"]["energy_kcal"]) == Decimal("300")
    assert Decimal(custom["custom"]["protein_g"]) == Decimal("8")
    assert Decimal(custom["last_quantity"]) == 2


def test_the_same_typed_in_name_is_one_recent_food(client, owner):
    _custom(client, owner, name="Chè đậu đen")
    _custom(client, owner, name="  chè đậu đen ")
    names = [r["custom"]["name"] for r in _recent(client, owner) if r["kind"] == "custom"]
    assert len(names) == 1


def test_recent_foods_are_the_callers_own(client, owner, stranger, catalog):
    _log(client, owner, catalog["dish"]["id"], "1", "serving")
    assert _recent(client, stranger) == []


def test_recent_foods_respect_the_limit(client, owner):
    for i in range(5):
        _custom(client, owner, name=f"Món {i}")
    assert len(_recent(client, owner, limit=3)) == 3


# --------------------------------------------------------------------------
# Ownership
# --------------------------------------------------------------------------


def test_another_users_item_is_not_found_rather_than_forbidden(
    client, owner, stranger, catalog
):
    """404, not 403 - answering 403 would confirm the id is real."""
    item = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()
    item_id = item["meal_item_id"]

    assert client.put(
        f"{API}/meal-items/{item_id}",
        headers=stranger,
        json={"quantity": "5", "unit": "serving"},
    ).status_code == 404
    assert client.delete(
        f"{API}/meal-items/{item_id}", headers=stranger
    ).status_code == 404


def test_a_day_shows_only_the_callers_own_meals(client, owner, stranger, catalog):
    _log(client, owner, catalog["dish"]["id"], "1", "serving")

    assert _day(client, stranger)["meals"] == []


def test_another_users_meal_is_not_found_rather_than_forbidden(
    client, owner, stranger, catalog
):
    meal_id = _meal(client, owner)

    assert client.put(
        f"{API}/meals/{meal_id}", headers=stranger, json={"name": "mine now"}
    ).status_code == 404
    assert client.delete(f"{API}/meals/{meal_id}", headers=stranger).status_code == 404
    # Nor can food be put into it.
    assert _log(
        client, stranger, catalog["dish"]["id"], "1", "serving", meal_id=meal_id
    ).status_code == 404


def test_another_users_item_status_is_not_found(client, owner, stranger, catalog):
    item = _log(
        client, owner, catalog["dish"]["id"], "1", "serving", status="planned"
    ).json()
    assert client.put(
        f"{API}/meal-items/{item['meal_item_id']}/status",
        headers=stranger,
        json={"status": "eaten"},
    ).status_code == 404


# --------------------------------------------------------------------------
# Targets and the history strip
# --------------------------------------------------------------------------


def test_setting_a_target_twice_replaces_rather_than_failing(client, owner):
    """Upserted in one statement: two devices saving at once must not hand the
    second one a unique-key error it did nothing to deserve."""
    first = client.put(
        f"{API}/nutrition-targets",
        headers=owner,
        json={"energy_kcal": "1800", "protein_g": "120"},
    )
    assert first.status_code == 200, first.text

    second = client.put(
        f"{API}/nutrition-targets", headers=owner, json={"energy_kcal": "2200"}
    )
    assert second.status_code == 200
    assert Decimal(second.json()["energy_kcal"]) == Decimal("2200")
    # Macros not resent are cleared, because PUT replaces the target rather
    # than patching it.
    assert second.json()["protein_g"] is None


def test_no_target_set_is_a_404(client, owner):
    assert client.get(f"{API}/nutrition-targets", headers=owner).status_code == 404


def test_the_summary_reports_one_row_per_day_logged(client, owner, catalog):
    _log(client, owner, catalog["dish"]["id"], "1", "serving", day=TODAY)
    _log(client, owner, catalog["dish"]["id"], "1", "serving", day=YESTERDAY)

    rows = client.get(
        f"{API}/nutrition/summary",
        headers=owner,
        params={"from": (TODAY - timedelta(days=7)).isoformat(), "to": TODAY.isoformat()},
    ).json()

    logged = {row["logged_date"] for row in rows}
    assert logged == {TODAY.isoformat(), YESTERDAY.isoformat()}
    # Days with nothing logged are absent rather than zero: a day the user did
    # not track is not a day they ate nothing.
    assert len(rows) == 2


def test_the_summary_refuses_a_backwards_range(client, owner):
    response = client.get(
        f"{API}/nutrition/summary",
        headers=owner,
        params={"from": TODAY.isoformat(), "to": YESTERDAY.isoformat()},
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Searching the catalog
# --------------------------------------------------------------------------


def test_search_ignores_accents(client, owner):
    """Typing Vietnamese accents on a phone while hungry is not something to
    require, so "bun cha" has to find "Bún chả"."""
    rows = client.get(
        f"{API}/foods", headers=owner, params={"search": "bun cha", "limit": 5}
    ).json()["items"]

    assert rows, "no results for an unaccented query"
    assert any("Bún chả" in row["name"] for row in rows)


def test_the_obvious_dish_is_not_buried_under_its_lookalikes(client, owner):
    """Unaccented, "pho" is a substring of "phô mai", "bánh phồng" and
    "Hải Phòng". Ordering by name alone put all of those above "Phở", which is
    the one dish somebody typing "pho" was plainly reaching for."""
    rows = client.get(
        f"{API}/foods", headers=owner, params={"search": "pho", "limit": 5}
    ).json()["items"]

    assert rows
    assert rows[0]["name"].lower().startswith("phở")


def test_a_repeated_dish_name_appears_once(client, owner):
    """The Institute surveyed "Cháo lòng" in nine places. Nine rows reading the
    same name is a list nobody can choose from, so the search returns one per
    (name, basis)."""
    rows = client.get(
        f"{API}/foods",
        headers=owner,
        params={"search": "chao long", "basis": "per_serving", "limit": 20},
    ).json()["items"]

    names = [row["name"] for row in rows]
    assert names.count("Cháo lòng") == 1


def test_the_surviving_duplicate_is_the_one_with_data(client, owner):
    """Taking the lowest id outright picked the one "Cháo lòng" of the nine
    whose label was never read — no ingredients, no serving weight, the least
    useful row of the set. The completest record wins instead."""
    rows = client.get(
        f"{API}/foods",
        headers=owner,
        params={"search": "chao long", "basis": "per_serving", "limit": 20},
    ).json()["items"]

    chosen = next(row for row in rows if row["name"] == "Cháo lòng")
    assert chosen["component_names"], "the row kept has no ingredient breakdown"
    assert chosen["serving_grams"] is not None


def test_a_dish_and_an_ingredient_of_the_same_name_appear_once(client, owner):
    """"Phở gà" was published both as a cooked bowl and as a per-100 g row.
    The dish with a serving weight wins: it has a picture, a recipe, and can
    be logged in servings or in grams, which covers what the ingredient row
    offered."""
    rows = client.get(
        f"{API}/foods", headers=owner, params={"search": "pho ga", "limit": 50}
    ).json()["items"]

    kept = [row for row in rows if row["name"] == "Phở gà"]
    assert len(kept) == 1
    assert kept[0]["basis"] == "per_serving"
    assert kept[0]["serving_grams"] is not None


def test_names_differing_only_in_punctuation_or_capitals_appear_once(client, owner):
    """"Gan lợn luộc" and "Gan lợn, luộc" are the same food."""
    rows = client.get(
        f"{API}/foods", headers=owner, params={"search": "gan lon", "limit": 50}
    ).json()["items"]

    normalised = [
        row["name"].lower().replace(",", "").replace("  ", " ") for row in rows
    ]
    assert normalised.count("gan lợn luộc") == 1


def test_the_unfiltered_catalog_has_no_repeated_names(client, owner):
    """What the picker shows before anything is typed. Paged through in full,
    no name appears twice."""
    seen: dict[str, int] = {}
    page = 1
    while True:
        body = client.get(
            f"{API}/foods", headers=owner, params={"page": page, "limit": 100}
        ).json()
        for row in body["items"]:
            key = " ".join(row["name"].lower().replace(",", " ").replace(".", " ").split())
            seen[key] = seen.get(key, 0) + 1
        if page * 100 >= body["total"]:
            break
        page += 1
    assert [name for name, n in seen.items() if n > 1] == []


def test_a_hidden_duplicate_is_still_reachable_for_old_diary_entries(
    client, owner, db_session
):
    """Duplicates are hidden, not deleted: an entry logged against one before
    the clean-up can still open its recipe and be rescaled."""
    hidden_id = db_session.execute(
        text("SELECT food_id FROM foods WHERE is_hidden AND basis = 'per_serving' "
             "AND serving_grams IS NOT NULL ORDER BY food_id LIMIT 1")
    ).scalar_one()

    assert client.get(f"{API}/foods/{hidden_id}", headers=owner).status_code == 200

    item = _log(client, owner, hidden_id, "1", "serving").json()
    edited = client.put(
        f"{API}/meal-items/{item['meal_item_id']}",
        headers=owner,
        json={"quantity": "2", "unit": "serving"},
    )
    assert edited.status_code == 200


def test_search_rows_carry_their_ingredients(client, owner):
    """The Institute publishes no description for any dish — the field exists
    and is empty in all 1247 — so the breakdown read off their label images is
    the only thing that tells two same-named bowls apart."""
    rows = client.get(
        f"{API}/foods",
        headers=owner,
        params={"search": "bun cha", "basis": "per_serving", "limit": 10},
    ).json()["items"]

    assert any(row["component_names"] for row in rows)


def test_bilingual_ingredient_names_lose_their_english_half(client, owner):
    """214 label cards print both languages, so 1323 of the 5663 ingredient
    rows read "Bánh phở/Pho (rice noodle)". On a Vietnamese screen the second
    half pushes the useful part off the line."""
    rows = client.get(
        f"{API}/foods",
        headers=owner,
        params={"search": "pho thin", "basis": "per_serving", "limit": 5},
    ).json()["items"]

    names = [n for row in rows for n in row["component_names"]]
    assert names, "no ingredients came back to check"
    assert not any("/" in n and n.split("/", 1)[1].isascii() for n in names)


def test_a_slash_inside_a_vietnamese_name_is_left_alone():
    """"Bánh rân bọc đường (60g/cái)" must keep its slash - cutting there
    leaves "(60g" dangling. The tail being Vietnamese is what tells the two
    cases apart."""
    from app.api.endpoints.foods import _vietnamese_only

    assert _vietnamese_only("Bánh phở/Pho (rice noodle)") == "Bánh phở"
    assert (
        _vietnamese_only("Bánh rân bọc đường (60g/cái)")
        == "Bánh rân bọc đường (60g/cái)"
    )
    assert _vietnamese_only("Nước dùng") == "Nước dùng"


def test_basis_is_an_exact_filter(client, owner):
    """Fuzzy is right for a search box and wrong for a filter."""
    rows = client.get(
        f"{API}/foods",
        headers=owner,
        params={"search": "com", "basis": "per_100g", "limit": 10},
    ).json()["items"]

    assert rows
    assert {row["basis"] for row in rows} == {"per_100g"}


def test_a_dish_carries_its_ingredients(client, owner, catalog):
    """The breakdown read off the Institute's own card - display detail the
    diary shows under a dish."""
    detail = client.get(
        f"{API}/foods/{catalog['dish']['id']}", headers=owner
    ).json()

    assert detail["basis"] == "per_serving"
    assert isinstance(detail["components"], list)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_a_nonexistent_food_is_a_404(client, owner):
    assert _log(client, owner, 99_999_999, "1", "serving").status_code == 404


@pytest.mark.parametrize("quantity", ["0", "-1", "999999"])
def test_an_impossible_amount_is_rejected(client, owner, catalog, quantity):
    assert _log(
        client, owner, catalog["dish"]["id"], quantity, "serving"
    ).status_code == 422


def test_an_unknown_status_is_rejected(client, owner, catalog):
    meal_id = _meal(client, owner)
    response = client.post(
        f"{API}/meal-items",
        headers=owner,
        json={
            "meal_id": meal_id,
            "food_id": catalog["dish"]["id"],
            "quantity": "1",
            "unit": "serving",
            "status": "skipped",
        },
    )
    assert response.status_code == 422
