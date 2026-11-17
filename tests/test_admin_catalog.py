"""Admin console: catalog maintenance and accounts.

Every write here changes what every app user sees, so the tests pin the
refusals as much as the writes: in-use rows are not deleted, a hidden food
stays hidden through the duplicate rule, a locked account stops working at
once rather than when its token expires.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import SessionLocal
from tests.conftest import API, PASSWORD
from tests.test_admin_mfa import _enroll_and_verify, _make_admin

PREFIX = "pytest-"


def _tag() -> str:
    return f"{PREFIX}{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
def _remove_catalog_rows() -> Iterator[None]:
    yield
    with SessionLocal() as db:
        for statement in (
            "DELETE FROM exercises WHERE exercise_id IN (SELECT exercise_id FROM exercise_translations "
            "WHERE name LIKE 'pytest-%') AND NOT EXISTS (SELECT 1 FROM workout_exercises w "
            "WHERE w.exercise_id = exercises.exercise_id)",
            "DELETE FROM foods WHERE food_id IN (SELECT food_id FROM food_translations WHERE name ILIKE 'pytest-%')",
            "DELETE FROM food_categories WHERE source_code LIKE 'pytest-%'",
            "DELETE FROM muscle_groups WHERE name LIKE 'pytest-%' AND parent_id IS NOT NULL",
            "DELETE FROM muscle_groups WHERE name LIKE 'pytest-%'",
            "DELETE FROM equipment WHERE name LIKE 'pytest-%'",
        ):
            db.execute(text(statement))
        db.commit()


@pytest.fixture
def admin(client: TestClient, credentials: Callable[[], dict]) -> dict:
    """Headers for an admin at aal2, plus the account's email and id."""
    creds = credentials()
    _make_admin(creds["email"])
    _, _, tokens = _enroll_and_verify(client, creds["headers"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    me = client.get(f"{API}/auth/me", headers=headers).json()
    return {"headers": headers, "email": creds["email"], "id": me.get("id") or me["user"]["id"]}


def _user_id(email: str) -> str:
    with SessionLocal() as db:
        return str(db.execute(text("SELECT id FROM auth.users WHERE email = :e"), {"e": email}).scalar_one())


# ------------------------------------------------------------------ guards


def test_every_route_needs_an_admin_at_aal2(
    client: TestClient, account: Callable[[], dict], credentials: Callable[[], dict]
):
    user = account()
    password_only = credentials()
    _make_admin(password_only["email"])
    for path in ("/admin/exercises", "/admin/foods", "/admin/users", "/admin/muscle-groups",
                 "/admin/equipment", "/admin/food-categories", "/admin/catalog-options"):
        assert client.get(f"{API}{path}", headers=user).status_code == 403
        refused = client.get(f"{API}{path}", headers=password_only["headers"])
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "mfa_enroll_required"


def test_catalog_options_lists_what_the_forms_pick_from(client: TestClient, admin: dict):
    body = client.get(f"{API}/admin/catalog-options", headers=admin["headers"]).json()
    assert body["tracking_types"] == ["reps", "duration", "distance", "reps_duration"]
    assert body["muscle_groups"] and body["equipment"]
    assert "Strength" in body["exercise_types"]


# ------------------------------------------------------------------ reference data


def test_muscle_group_crud_with_translations(client: TestClient, admin: dict):
    h = admin["headers"]
    name = _tag()
    created = client.post(f"{API}/admin/muscle-groups", headers=h, json={
        "name": name, "translations": [{"language": "vi", "name": "Cơ thử"}, {"language": "en", "name": "Test"}],
    })
    assert created.status_code == 201, created.text
    mid = created.json()["muscle_id"]
    assert {t["language"] for t in created.json()["translations"]} == {"vi", "en"}

    duplicate = client.post(f"{API}/admin/muscle-groups", headers=h, json={"name": name})
    assert duplicate.status_code == 409
    assert "Đã có nhóm cơ" in duplicate.json()["detail"]

    # Leaving English out of the list removes it.
    updated = client.put(f"{API}/admin/muscle-groups/{mid}", headers=h, json={
        "name": name, "translations": [{"language": "vi", "name": "Cơ thử 2"}],
    }).json()
    assert updated["translations"] == [{"language": "vi", "name": "Cơ thử 2", "description": None}]

    child = client.post(f"{API}/admin/muscle-groups", headers=h, json={"name": _tag(), "parent_id": mid}).json()
    loop = client.put(f"{API}/admin/muscle-groups/{mid}", headers=h, json={"name": name, "parent_id": child["muscle_id"]})
    assert loop.status_code == 409
    blocked = client.delete(f"{API}/admin/muscle-groups/{mid}", headers=h)
    assert blocked.status_code == 409 and "nhóm cha" in blocked.json()["detail"]

    assert client.delete(f"{API}/admin/muscle-groups/{child['muscle_id']}", headers=h).status_code == 204
    assert client.delete(f"{API}/admin/muscle-groups/{mid}", headers=h).status_code == 204
    assert client.get(f"{API}/admin/muscle-groups/{mid}", headers=h).status_code == 404


def test_reference_rows_in_use_are_not_deleted(client: TestClient, admin: dict):
    h = admin["headers"]
    muscle = client.post(f"{API}/admin/muscle-groups", headers=h, json={"name": _tag()}).json()
    gear = client.post(f"{API}/admin/equipment", headers=h, json={"name": _tag()}).json()
    client.post(f"{API}/admin/exercises", headers=h, json={
        "translations": [{"language": "en", "name": _tag()}],
        "muscles": [{"muscle_id": muscle["muscle_id"]}],
        "equipment_ids": [gear["equipment_id"]],
    }).raise_for_status()

    for path in (f"muscle-groups/{muscle['muscle_id']}", f"equipment/{gear['equipment_id']}"):
        refused = client.delete(f"{API}/admin/{path}", headers=h)
        assert refused.status_code == 409
        assert "1 bài tập" in refused.json()["detail"]


def test_deleting_a_food_category_keeps_its_foods(client: TestClient, admin: dict):
    h = admin["headers"]
    category = client.post(f"{API}/admin/food-categories", headers=h, json={
        "source_code": _tag(), "name_vi": "Nhóm thử",
    })
    assert category.status_code == 201, category.text
    cid = category.json()["food_category_id"]
    food = client.post(f"{API}/admin/foods", headers=h, json={
        "basis": "per_100g", "energy_kcal": 100, "food_category_id": cid,
        "translations": [{"language": "vi", "name": _tag()}],
    }).json()
    assert client.get(f"{API}/admin/food-categories/{cid}", headers=h).json()["food_count"] == 1

    assert client.delete(f"{API}/admin/food-categories/{cid}", headers=h).status_code == 204
    after = client.get(f"{API}/admin/foods/{food['food_id']}", headers=h).json()
    assert after["food_category_id"] is None


# ------------------------------------------------------------------ exercises


def test_an_exercise_written_here_is_what_the_app_reads(client: TestClient, admin: dict, account):
    h = admin["headers"]
    muscles = client.get(f"{API}/admin/muscle-groups", headers=h).json()
    name = _tag()
    created = client.post(f"{API}/admin/exercises", headers=h, json={
        "exercise_type": "Strength",
        "tracking_type": "duration",
        "ai_algorithm": "pushup_v1",  # not writable: ignored
        "translations": [
            {"language": "vi", "name": name + " vi", "instructions": "Giữ thẳng lưng"},
            {"language": "en", "name": name + " en"},
        ],
        "muscles": [{"muscle_id": muscles[0]["muscle_id"], "role": "primary"},
                    {"muscle_id": muscles[1]["muscle_id"], "role": "secondary"}],
        "media": [{"media_type": "thumbnail", "storage_key": "pytest/thumb.jpg"}],
    })
    assert created.status_code == 201, created.text
    body = created.json()
    eid = body["exercise_id"]
    assert body["ai_algorithm"] is None
    assert body["media"][0]["url"]
    media_id = body["media"][0]["media_id"]

    public = client.get(f"{API}/exercises/{eid}?language=vi", headers=account()).json()
    assert public["name"] == name + " vi"
    assert public["tracking_type"] == "duration"
    assert public["instructions"] == "Giữ thẳng lưng"

    # Replacing the lists: the thumbnail survives with its id, a video is
    # added, one muscle and the English translation go.
    updated = client.put(f"{API}/admin/exercises/{eid}", headers=h, json={
        "tracking_type": "reps",
        "translations": [{"language": "vi", "name": name + " vi"}],
        "muscles": [{"muscle_id": muscles[0]["muscle_id"], "role": "primary"}],
        "media": [{"media_type": "thumbnail", "storage_key": "pytest/thumb.jpg"},
                  {"media_type": "video", "storage_key": "pytest/clip.mp4"}],
    }).json()
    assert [t["language"] for t in updated["translations"]] == ["vi"]
    assert len(updated["muscles"]) == 1
    kept = [m for m in updated["media"] if m["media_type"] == "thumbnail"]
    assert kept[0]["media_id"] == media_id
    assert len(updated["media"]) == 2

    listed = client.get(f"{API}/admin/exercises", headers=h, params={"search": name}).json()
    assert listed["total"] == 1 and listed["items"][0]["name_vi"] == name + " vi"

    bad_key = client.put(f"{API}/admin/exercises/{eid}", headers=h, json={
        "translations": [{"language": "vi", "name": name}],
        "media": [{"media_type": "video", "storage_key": "https://example.com/a.mp4"}],
    })
    assert bad_key.status_code == 422

    assert client.delete(f"{API}/admin/exercises/{eid}", headers=h).status_code == 204
    assert client.get(f"{API}/exercises/{eid}").status_code == 404


def test_an_exercise_in_a_plan_is_not_deleted(client: TestClient, admin: dict, owner: dict):
    h = admin["headers"]
    eid = client.post(f"{API}/admin/exercises", headers=h, json={
        "translations": [{"language": "en", "name": _tag()}],
    }).json()["exercise_id"]
    plan = client.post(f"{API}/workout-plans", headers=owner, json={"name": "Pytest"}).json()
    day = client.post(f"{API}/workout-plans/{plan['plan_id']}/days", headers=owner, json={"name": "A"}).json()
    client.post(f"{API}/workout-days/{day['workout_day_id']}/exercises", headers=owner,
                json={"exercise_id": eid}).raise_for_status()

    refused = client.delete(f"{API}/admin/exercises/{eid}", headers=h)
    assert refused.status_code == 409
    assert "kế hoạch tập" in refused.json()["detail"]
    assert client.get(f"{API}/admin/exercises/{eid}", headers=h).json()["usage"]["plan_exercises"] == 1

    client.delete(f"{API}/workout-plans/{plan['plan_id']}", headers=owner).raise_for_status()
    assert client.delete(f"{API}/admin/exercises/{eid}", headers=h).status_code == 204


# ------------------------------------------------------------------ foods


def _food(name: str, **extra) -> dict:
    return {"basis": "per_serving", "energy_kcal": 450,
            "translations": [{"language": "vi", "name": name}], **extra}


def test_a_food_keeps_unknown_macros_null_and_is_searchable(client: TestClient, admin: dict, account):
    h = admin["headers"]
    name = _tag()
    created = client.post(f"{API}/admin/foods", headers=h, json=_food(
        name, protein_g=20, serving_grams=350,
        components=[{"name_vi": "Bánh phở", "grams": 200}, {"name_vi": "Hành"}],
    ))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["fat_g"] is None and body["carb_g"] is None
    assert body["serving_source"] == "admin"
    assert [c["name_vi"] for c in body["components"]] == ["Bánh phở", "Hành"]
    assert body["visibility"] == "auto" and body["is_hidden"] is False

    found = client.get(f"{API}/foods", headers=account(), params={"search": name}).json()
    assert [f["food_id"] for f in found["items"]] == [body["food_id"]]

    no_vi = client.post(f"{API}/admin/foods", headers=h, json={
        "basis": "per_100g", "energy_kcal": 1, "translations": [{"language": "en", "name": _tag()}],
    })
    assert no_vi.status_code == 422


def test_the_duplicate_rule_applies_and_an_admin_override_wins(client: TestClient, admin: dict):
    h = admin["headers"]
    name = _tag()
    plain = client.post(f"{API}/admin/foods", headers=h, json=_food(name)).json()
    # Same name, more complete (a serving weight): it takes the name, the
    # first one is hidden at once.
    better = client.post(f"{API}/admin/foods", headers=h, json=_food(name.upper(), serving_grams=300)).json()
    assert better["is_hidden"] is False
    assert client.get(f"{API}/admin/foods/{plain['food_id']}", headers=h).json()["is_hidden"] is True

    # The admin's word beats the rule, and survives the script being re-run.
    shown = client.put(f"{API}/admin/foods/{plain['food_id']}", headers=h,
                       json=_food(name, visibility="shown")).json()
    assert shown["is_hidden"] is False and shown["visibility"] == "shown"
    from app.services import food_visibility
    with SessionLocal() as db:
        food_visibility.recompute(db)
        db.commit()
    assert client.get(f"{API}/admin/foods/{plain['food_id']}", headers=h).json()["is_hidden"] is False

    back = client.put(f"{API}/admin/foods/{plain['food_id']}", headers=h, json=_food(name)).json()
    assert back["visibility"] == "auto" and back["is_hidden"] is True

    hidden = client.get(f"{API}/admin/foods", headers=h, params={"search": name, "shown": "hidden"}).json()
    assert [f["food_id"] for f in hidden["items"]] == [plain["food_id"]]


def test_deleting_a_food_leaves_the_diary_entry(client: TestClient, admin: dict, owner: dict):
    h = admin["headers"]
    food = client.post(f"{API}/admin/foods", headers=h, json=_food(_tag())).json()
    meal = client.post(f"{API}/meals", headers=owner, json={"logged_date": "2026-11-01"})
    assert meal.status_code == 201, meal.text
    item = client.post(f"{API}/meal-items", headers=owner, json={
        "meal_id": meal.json()["meal_id"], "food_id": food["food_id"],
        "quantity": 1, "unit": "serving", "status": "eaten",
    })
    assert item.status_code == 201, item.text
    assert client.get(f"{API}/admin/foods/{food['food_id']}", headers=h).json()["usage"]["diary_entries"] == 1

    assert client.delete(f"{API}/admin/foods/{food['food_id']}", headers=h).status_code == 204
    day = client.get(f"{API}/meals", headers=owner, params={"date": "2026-11-01"}).json()
    entries = [i for m in day["meals"] for i in m["items"]]
    assert len(entries) == 1
    assert entries[0]["food_id"] is None and float(entries[0]["energy_kcal"]) == 450


# ------------------------------------------------------------------ accounts


def test_listing_and_changing_roles(client: TestClient, admin: dict, credentials):
    h = admin["headers"]
    other = credentials()
    listed = client.get(f"{API}/admin/users", headers=h, params={"search": other["email"]}).json()
    assert listed["total"] == 1
    row = listed["items"][0]
    assert row["role"] == "user" and row["is_seeded"] is False

    own = client.patch(f"{API}/admin/users/{_user_id(admin['email'])}/role", headers=h, json={"role": "user"})
    assert own.status_code == 409

    promoted = client.patch(f"{API}/admin/users/{row['user_id']}/role", headers=h, json={"role": "admin"})
    assert promoted.status_code == 200 and promoted.json()["role"] == "admin"
    admins = client.get(f"{API}/admin/users", headers=h, params={"role": "admin", "search": other["email"]}).json()
    assert admins["total"] == 1

    demoted = client.patch(f"{API}/admin/users/{row['user_id']}/role", headers=h, json={"role": "user"})
    assert demoted.json()["role"] == "user"
    detail = client.get(f"{API}/admin/users/{row['user_id']}", headers=h).json()
    assert detail["has_profile"] is True and "email" in detail["providers"]


def test_a_locked_account_stops_working_at_once(client: TestClient, admin: dict, credentials):
    h = admin["headers"]
    victim = credentials()
    uid = _user_id(victim["email"])
    assert client.get(f"{API}/profile", headers=victim["headers"]).status_code == 200

    locked = client.post(f"{API}/admin/users/{uid}/ban", headers=h, json={})
    assert locked.status_code == 200, locked.text
    assert locked.json()["is_banned"] is True
    # The token it already holds, not just new sign-ins.
    assert client.get(f"{API}/profile", headers=victim["headers"]).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": victim["email"], "password": PASSWORD}).status_code >= 400

    unlocked = client.post(f"{API}/admin/users/{uid}/unban", headers=h)
    assert unlocked.json()["is_banned"] is False
    assert client.post(f"{API}/auth/login", json={"email": victim["email"], "password": PASSWORD}).status_code == 200

    assert client.post(f"{API}/admin/users/{_user_id(admin['email'])}/ban", headers=h, json={}).status_code == 409


def test_cors_allows_the_admin_origin_only(client: TestClient):
    preflight = {"Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"}
    ok = client.options(f"{API}/admin/users", headers={"Origin": "http://localhost:5173", **preflight})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    other = client.options(f"{API}/admin/users", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in other.headers
