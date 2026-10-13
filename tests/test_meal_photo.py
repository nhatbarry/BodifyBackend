"""Meal photos, and the draft a photo of a plate turns into.

The model is not called here: every analysis test replaces
`analyze_food_photo` with a stub returning a fixed answer, the same way the
coach tests stub `generate_reply`. Storage is stubbed too, so these run with
no R2 credentials. What is under test is everything around them - what the
server stores, what it refuses, and above all that **a dish the catalog has is
priced from the catalog, not from the model's guess**.
"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import text

from app.api.endpoints import meals as meals_endpoint
from app.core.config import settings
from app.services import food_photo, gemini, meal_photo
from app.services.meal_photo import PHOTO_MAX_SIDE, process_photo
from tests.conftest import API

TODAY = date(2026, 3, 2)


def _photo(width: int = 1600, height: int = 1200, exif: bytes | None = None) -> bytes:
    image = Image.new("RGB", (width, height), (200, 120, 60))
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, "JPEG", exif=exif)
    else:
        image.save(buffer, "JPEG")
    return buffer.getvalue()


def _files(raw: bytes | None = None):
    return {"file": ("plate.jpg", raw if raw is not None else _photo(), "image/jpeg")}


# --------------------------------------------------------------------------
# Stubs
# --------------------------------------------------------------------------


@pytest.fixture
def storage(monkeypatch):
    """An in-memory bucket. Records what was put and what was deleted."""
    bucket = {"put": {}, "deleted": []}

    def put(key, data, content_type):
        bucket["put"][key] = data

    def delete(key):
        bucket["deleted"].append(key)

    monkeypatch.setattr(meal_photo, "put_object", put)
    monkeypatch.setattr(meal_photo, "delete_object", delete)
    if not settings.storage_write_bucket:
        monkeypatch.setattr(settings, "storage_write_bucket", "pytest-uploads")
    return bucket


@pytest.fixture
def model(monkeypatch):
    """Replace the model with a stub; set `model.answer` per test."""

    class Stub:
        answer: dict = {"is_food": True, "items": []}
        error: Exception | None = None
        calls = 0

    async def fake(image, mime_type):
        Stub.calls += 1
        if Stub.error is not None:
            raise Stub.error
        return Stub.answer

    monkeypatch.setattr(meals_endpoint, "analyze_food_photo", fake)
    monkeypatch.setattr(settings, "gemini_api_key", "pytest-key")
    monkeypatch.setattr(settings, "food_photo_daily_limit", 5)
    return Stub


@pytest.fixture(scope="module")
def catalog():
    """A visible dish with a serving weight and a visible ingredient, by name -
    the names are what the model would say."""
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        dish = db.execute(text("""
            SELECT f.food_id, f.energy_kcal, t.name
            FROM foods f JOIN food_translations t
              ON t.food_id = f.food_id AND t.language_code = 'vi'
            WHERE f.basis = 'per_serving' AND f.serving_grams IS NOT NULL
              AND NOT f.is_hidden AND length(t.name) > 6
            ORDER BY f.food_id LIMIT 1
        """)).one()
        ingredient = db.execute(text("""
            SELECT f.food_id, f.energy_kcal, t.name
            FROM foods f JOIN food_translations t
              ON t.food_id = f.food_id AND t.language_code = 'vi'
            WHERE f.basis = 'per_100g' AND NOT f.is_hidden AND f.energy_kcal > 0
              AND length(t.name) > 6 AND position('/' in t.name) = 0
            ORDER BY f.food_id LIMIT 1
        """)).one()
    return {
        "dish": {"id": dish[0], "kcal": Decimal(dish[1]), "name": dish[2]},
        "ingredient": {"id": ingredient[0], "kcal": Decimal(ingredient[1]), "name": ingredient[2]},
    }


def _meal(client, headers):
    response = client.post(
        f"{API}/meals", headers=headers, json={"logged_date": TODAY.isoformat()}
    )
    assert response.status_code == 201, response.text
    return response.json()["meal_id"]


def _analyze(client, headers, meal_id, raw: bytes | None = None):
    return client.post(
        f"{API}/meals/{meal_id}/photo-analysis", headers=headers, files=_files(raw)
    )


def _item(name, servings=1, grams=400, kcal=999, confidence=0.9, **extra):
    return {
        "name_vi": name,
        "servings": servings,
        "grams": grams,
        "energy_kcal": kcal,
        "confidence": confidence,
        **extra,
    }


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------


def test_a_meal_photo_is_shrunk_but_not_cropped():
    """A plate photo is evidence of what was eaten; cropping it to a square
    the way avatars are could cut a dish off the edge."""
    with Image.open(io.BytesIO(process_photo(_photo(4000, 3000)))) as result:
        assert result.format == "WEBP"
        assert result.size == (PHOTO_MAX_SIDE, 960)


def test_a_small_photo_is_not_enlarged():
    with Image.open(io.BytesIO(process_photo(_photo(640, 480)))) as result:
        assert result.size == (640, 480)


def test_a_meal_photo_loses_its_exif():
    """The photo of dinner goes to Google; it must not say where home is."""
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"  # Make
    with Image.open(io.BytesIO(process_photo(_photo(exif=exif.tobytes())))) as result:
        assert not result.getexif()


# --------------------------------------------------------------------------
# The photo on a meal
# --------------------------------------------------------------------------


def test_upload_then_remove_a_meal_photo(client, owner, storage):
    meal_id = _meal(client, owner)

    first = client.post(f"{API}/meals/{meal_id}/photo", headers=owner, files=_files())
    assert first.status_code == 200, first.text
    first_key = first.json()["photo_key"]
    assert first_key.startswith("meal/") and first.json()["photo_url"]

    # Replacing deletes the old object, and only after the new one is up.
    second = client.post(f"{API}/meals/{meal_id}/photo", headers=owner, files=_files())
    assert second.json()["photo_key"] != first_key
    assert storage["deleted"] == [first_key]

    removed = client.delete(f"{API}/meals/{meal_id}/photo", headers=owner)
    assert removed.json()["photo_key"] is None
    assert removed.json()["photo_url"] is None
    assert storage["deleted"] == [first_key, second.json()["photo_key"]]


def test_a_photo_key_cannot_be_set_by_hand(client, owner, storage):
    """It used to be writable, which let a client point its meal at anybody's
    object and have this server sign it."""
    meal_id = _meal(client, owner)
    response = client.put(
        f"{API}/meals/{meal_id}",
        headers=owner,
        json={"photo_key": "avatar/someone-else/x.webp", "note": "lunch"},
    )
    assert response.status_code == 200
    assert response.json()["photo_key"] is None
    assert response.json()["note"] == "lunch"


def test_deleting_a_meal_deletes_its_photo(client, owner, storage):
    meal_id = _meal(client, owner)
    key = client.post(
        f"{API}/meals/{meal_id}/photo", headers=owner, files=_files()
    ).json()["photo_key"]

    assert client.delete(f"{API}/meals/{meal_id}", headers=owner).status_code == 204
    assert storage["deleted"] == [key]


def test_a_non_image_is_refused_with_422(client, owner, storage):
    meal_id = _meal(client, owner)
    response = client.post(
        f"{API}/meals/{meal_id}/photo", headers=owner, files=_files(b"not a picture")
    )
    assert response.status_code == 422
    assert storage["put"] == {}


def test_another_users_meal_answers_404(client, owner, stranger, storage, model):
    meal_id = _meal(client, owner)
    assert client.post(
        f"{API}/meals/{meal_id}/photo", headers=stranger, files=_files()
    ).status_code == 404
    assert _analyze(client, stranger, meal_id).status_code == 404
    assert model.calls == 0


# --------------------------------------------------------------------------
# The draft
# --------------------------------------------------------------------------


def test_a_dish_the_catalog_has_is_priced_from_the_catalog(
    client, owner, storage, model, catalog
):
    """The model's 999 kcal is a guess from pixels; the Institute's figure was
    measured. The measurement wins."""
    dish = catalog["dish"]
    model.answer = {"is_food": True, "items": [_item(dish["name"], servings=2, kcal=999)]}

    meal_id = _meal(client, owner)
    response = _analyze(client, owner, meal_id)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["meal"]["photo_url"]
    detected = body["items"][0]
    first = detected["candidates"][0]
    assert first["food"]["food_id"] == dish["id"]
    assert first["unit"] == "serving"
    assert Decimal(first["quantity"]) == 2
    assert Decimal(first["energy_kcal"]) == (dish["kcal"] * 2).quantize(Decimal("0.01"))

    # The guess is still there, per serving, for when the user rejects the match.
    assert Decimal(detected["estimate"]["energy_kcal"]) == Decimal("499.50")


def test_an_ingredient_is_suggested_in_grams(client, owner, storage, model, catalog):
    """A per-100 g row refuses servings with a 422; the draft must never
    propose what the diary would refuse."""
    ingredient = catalog["ingredient"]
    model.answer = {"is_food": True, "items": [_item(ingredient["name"], grams=152)]}

    body = _analyze(client, owner, _meal(client, owner)).json()
    first = body["items"][0]["candidates"][0]
    assert first["food"]["food_id"] == ingredient["id"]
    assert first["unit"] == "gram"
    assert Decimal(first["quantity"]) == 150  # rounded to 5 g


def test_an_unknown_dish_has_only_the_estimate(client, owner, storage, model):
    model.answer = {
        "is_food": True,
        "items": [_item("Zzqx món không tồn tại", servings=2, kcal=800, protein_g=40)],
    }
    detected = _analyze(client, owner, _meal(client, owner)).json()["items"][0]

    assert detected["candidates"] == []
    assert Decimal(detected["estimate"]["quantity"]) == 2
    assert Decimal(detected["estimate"]["energy_kcal"]) == 400
    assert Decimal(detected["estimate"]["protein_g"]) == 20


def test_analysis_writes_nothing_to_the_diary(client, owner, storage, model, catalog):
    """A draft until the user confirms it."""
    model.answer = {"is_food": True, "items": [_item(catalog["dish"]["name"])]}
    meal_id = _meal(client, owner)
    _analyze(client, owner, meal_id)

    day = client.get(f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}).json()
    meal = next(m for m in day["meals"] if m["meal_id"] == meal_id)
    assert meal["items"] == []


def test_not_food_comes_back_as_such(client, owner, storage, model):
    model.answer = {"is_food": False, "items": []}
    body = _analyze(client, owner, _meal(client, owner)).json()
    assert body["is_food"] is False and body["items"] == []


def test_the_models_numbers_are_clamped_before_use():
    """Structured output guarantees JSON, not sense."""
    is_food, detected = food_photo.clean({
        "is_food": True,
        "items": [
            _item("Cơm", servings=0, grams=40000, kcal=500),
            _item("", kcal=100),                       # no name: dropped
            {"name_vi": "Canh", "servings": 1},        # no energy: dropped
            _item("Bánh", servings="abc", grams=-5, kcal=200, confidence=7),
        ],
    })
    assert is_food
    assert [d.name for d in detected] == ["Cơm", "Bánh"]
    assert detected[0].servings == Decimal("0.25")
    assert detected[0].grams == Decimal("3000")
    assert detected[1].servings == Decimal("1")
    assert detected[1].grams is None
    assert detected[1].confidence == 1.0


# --------------------------------------------------------------------------
# Limits and failures
# --------------------------------------------------------------------------


def test_the_daily_limit_answers_429_before_uploading(
    client, owner, storage, model, monkeypatch
):
    monkeypatch.setattr(settings, "food_photo_daily_limit", 1)
    meal_id = _meal(client, owner)
    assert _analyze(client, owner, meal_id).status_code == 201

    puts = len(storage["put"])
    refused = _analyze(client, owner, meal_id)
    assert refused.status_code == 429
    assert len(storage["put"]) == puts
    assert model.calls == 1


def test_a_failed_analysis_keeps_the_photo_and_does_not_count(
    client, owner, storage, model, monkeypatch
):
    """The user still has the photo they took, and a request that gave them
    nothing does not spend their allowance."""
    monkeypatch.setattr(settings, "food_photo_daily_limit", 1)
    model.error = gemini.FoodPhotoUnavailable("Photo analysis took too long to answer.")
    meal_id = _meal(client, owner)

    failed = _analyze(client, owner, meal_id)
    assert failed.status_code == 503
    day = client.get(f"{API}/meals", headers=owner, params={"date": TODAY.isoformat()}).json()
    assert next(m for m in day["meals"] if m["meal_id"] == meal_id)["photo_url"]

    model.error = None
    assert _analyze(client, owner, meal_id).status_code == 201


def test_googles_quota_is_429_not_503(client, owner, storage, model):
    model.error = gemini.FoodPhotoQuotaExceeded("Hôm nay máy chủ đã dùng hết lượt.")
    assert _analyze(client, owner, _meal(client, owner)).status_code == 429


# --------------------------------------------------------------------------
# Saving the draft
# --------------------------------------------------------------------------


def test_saved_items_point_back_at_their_analysis(
    client, owner, stranger, storage, model, catalog
):
    model.answer = {"is_food": True, "items": [_item(catalog["dish"]["name"])]}
    meal_id = _meal(client, owner)
    body = _analyze(client, owner, meal_id).json()
    analysis_id = body["analysis_id"]
    candidate = body["items"][0]["candidates"][0]

    logged = client.post(f"{API}/meal-items", headers=owner, json={
        "meal_id": meal_id,
        "food_id": candidate["food"]["food_id"],
        "quantity": candidate["quantity"],
        "unit": candidate["unit"],
        "photo_analysis_id": analysis_id,
    })
    assert logged.status_code == 201, logged.text
    assert logged.json()["photo_analysis_id"] == analysis_id

    custom = client.post(f"{API}/meal-items/custom", headers=owner, json={
        "meal_id": meal_id, "name": "Món lạ", "energy_kcal": "300",
        "photo_analysis_id": analysis_id,
    })
    assert custom.status_code == 201
    assert custom.json()["photo_analysis_id"] == analysis_id

    # Somebody else's analysis id is not found, not forbidden.
    other_meal = _meal(client, stranger)
    assert client.post(f"{API}/meal-items/custom", headers=stranger, json={
        "meal_id": other_meal, "name": "x", "energy_kcal": "1",
        "photo_analysis_id": analysis_id,
    }).status_code == 404


# --------------------------------------------------------------------------
# Matching: measured on a real photo of cơm tấm
# --------------------------------------------------------------------------


def test_a_match_must_start_with_the_name(client, owner, storage, model):
    """"mỡ hành" once matched "Sò huyết nướng mỡ hành" as a substring - a
    different dish, preselected. A draft has to be stricter than a search box."""
    model.answer = {"is_food": True, "items": [_item("Mỡ hành", kcal=60)]}
    detected = _analyze(client, owner, _meal(client, owner)).json()["items"][0]
    for candidate in detected["candidates"]:
        assert candidate["food"]["name"].lower().startswith("mỡ hành")


def test_a_one_word_fallback_is_not_searched(client, owner, storage, model):
    """The fallback "Cơm" put "Cơm gà" (1140 kcal) on top for plain rice."""
    model.answer = {"is_food": True, "items": [
        _item("Zzqx cơm lạ", kcal=260, search_terms=["Cơm"]),
    ]}
    detected = _analyze(client, owner, _meal(client, owner)).json()["items"][0]
    assert detected["candidates"] == []


def test_a_wildly_different_energy_is_flagged_not_preselected():
    assert food_photo._plausible(Decimal("431.7"), Decimal("450"))
    assert not food_photo._plausible(Decimal("1140.4"), Decimal("260"))
    assert food_photo._plausible(Decimal("0"), Decimal("16"))
