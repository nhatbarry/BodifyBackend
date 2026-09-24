"""The food diary.

Every test here pins a decision that was argued, not a coincidence of the
current code: why a per-100 g row refuses to be logged in servings, why
correcting the catalog must not move yesterday's total, why two foods added to
lunch land in one lunch.

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


def _log(client, headers, food_id, quantity, unit, meal_type="lunch", day=TODAY):
    return client.post(
        f"{API}/meal-items",
        headers=headers,
        json={
            "logged_date": day.isoformat(),
            "meal_type": meal_type,
            "food_id": food_id,
            "quantity": str(quantity),
            "unit": unit,
        },
    )


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


def test_two_foods_in_one_sitting_land_in_one_meal(client, owner, catalog):
    """There is exactly one lunch on a day. Adding a second food appends to it
    rather than creating a rival lunch the screen would render twice."""
    _log(client, owner, catalog["dish"]["id"], "1", "serving")
    _log(client, owner, catalog["ingredient"]["id"], "100", "gram")

    day = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()

    lunches = [m for m in day["meals"] if m["meal_type"] == "lunch"]
    assert len(lunches) == 1
    assert len(lunches[0]["items"]) == 2


def test_a_day_totals_its_items_and_subtracts_the_target(client, owner, catalog):
    client.put(
        f"{API}/nutrition-targets", headers=owner, json={"energy_kcal": "2000"}
    )
    _log(client, owner, catalog["dish"]["id"], "1", "serving", meal_type="breakfast")

    day = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()

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


def test_removing_the_last_food_removes_the_empty_sitting(client, owner, catalog):
    """An empty lunch is not something the user logged; it would render as a
    heading with nothing under it."""
    item = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()

    assert client.delete(
        f"{API}/meal-items/{item['meal_item_id']}", headers=owner
    ).status_code == 204

    day = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()
    assert [m for m in day["meals"] if m["meal_type"] == "lunch"] == []


def test_a_sitting_with_a_note_survives_losing_its_last_food(
    client, owner, catalog
):
    """A note is the user's own writing, not a by-product of the items."""
    item = _log(client, owner, catalog["dish"]["id"], "1", "serving").json()
    day = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()
    meal_id = day["meals"][0]["meal_id"]

    client.put(
        f"{API}/meals/{meal_id}", headers=owner, json={"note": "ăn ở nhà bà ngoại"}
    )
    client.delete(f"{API}/meal-items/{item['meal_item_id']}", headers=owner)

    after = client.get(
        f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}
    ).json()
    kept = [m for m in after["meals"] if m["meal_id"] == meal_id]
    assert len(kept) == 1
    assert kept[0]["note"] == "ăn ở nhà bà ngoại"
    assert kept[0]["items"] == []


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

    day = client.get(
        f"{API}/meals", headers=stranger, params={"date": TODAY.isoformat()}
    ).json()
    assert day["meals"] == []


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


def test_a_dish_and_an_ingredient_of_the_same_name_both_survive(client, owner):
    """"Phở gà" is both a cooked bowl and a per-100 g row. They are measured
    differently and logged differently; collapsing them would hide one."""
    rows = client.get(
        f"{API}/foods", headers=owner, params={"search": "pho ga", "limit": 20}
    ).json()["items"]

    bases = {row["basis"] for row in rows if row["name"] == "Phở gà"}
    assert bases == {"per_serving", "per_100g"}


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


def test_an_unknown_meal_type_is_rejected(client, owner, catalog):
    response = client.post(
        f"{API}/meal-items",
        headers=owner,
        json={
            "logged_date": TODAY.isoformat(),
            "meal_type": "brunch",
            "food_id": catalog["dish"]["id"],
            "quantity": "1",
            "unit": "serving",
        },
    )
    assert response.status_code == 422
