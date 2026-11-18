"""Personal data is masked by the database for everyone but a superadmin.

The admin requests of any other staff member run under the role
`bodify_pii_masked` (sql_script/ddl_pii_masking.sql, app/db/pii.py), which
cannot read auth.*, user_profiles or free text people typed. These tests need
that script applied to the database they run against.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.core.permissions import ALL_PERMISSIONS
from app.db.pii import mask_personal_data
from app.db.session import SessionLocal
from app.main import app
from app.services import admin_analytics
from tests.conftest import API
from tests.test_admin_roles import (  # noqa: F401 - fixtures
    _api_routes,
    _remove_test_roles,
    _user_id,
    make_role,
    make_staff,
    superadmin,
)

FULL_NAME = "Nguyễn Văn Pytest"


@pytest.fixture
def subject(client: TestClient, credentials: Callable[[], dict]) -> dict:
    """A regular account with a full name and a date of birth."""
    creds = credentials()
    saved = client.put(f"{API}/profile", headers=creds["headers"],
                       json={"name": FULL_NAME, "date_of_birth": "1990-05-01"})
    assert saved.status_code == 200, saved.text
    return {"email": creds["email"], "id": _user_id(creds["email"])}


@pytest.fixture
def staff(make_role, make_staff) -> dict:
    """An admin holding every permission - still not a superadmin."""
    return make_staff(make_role(*sorted(ALL_PERMISSIONS))["admin_role_id"])


def test_staff_read_masked_values_and_a_superadmin_the_real_ones(
    client: TestClient, subject: dict, staff: dict, superadmin: dict,
):
    seen = client.get(f"{API}/admin/users/{subject['id']}", headers=staff["headers"])
    assert seen.status_code == 200, seen.text
    body = seen.json()
    local, domain = subject["email"].split("@")
    assert body["email"] == f"{local[:2]}***@{domain}"
    assert body["name"] == "Nguyễn V***"
    assert body["date_of_birth"] is None and body["birth_year"] == 1990
    assert body["avatar_url"] is None
    assert body["pii_masked"] is True
    # What is not personal still reads as it is.
    assert body["providers"] == ["email"] and body["has_profile"] is True

    real = client.get(f"{API}/admin/users/{subject['id']}", headers=superadmin["headers"]).json()
    assert real["email"] == subject["email"] and real["name"] == FULL_NAME
    assert real["date_of_birth"] == "1990-05-01" and real["pii_masked"] is False


def test_the_list_is_masked_too_and_a_whole_address_still_finds_the_account(
    client: TestClient, subject: dict, staff: dict,
):
    found = client.get(f"{API}/admin/users", headers=staff["headers"],
                       params={"search": subject["email"]}).json()
    assert [i["user_id"] for i in found["items"]] == [subject["id"]]
    assert found["items"][0]["email"] != subject["email"]

    # Part of the real address that the masked one does not show finds nothing:
    # a partial search must not become a way to read an address letter by letter.
    hidden_part = subject["email"].split("@")[0][2:]
    partial = client.get(f"{API}/admin/users", headers=staff["headers"],
                         params={"search": hidden_part}).json()
    assert subject["id"] not in [i["user_id"] for i in partial["items"]]


def test_the_masked_role_is_refused_the_raw_tables():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        mask_personal_data(db)
        for statement in (
            "SELECT email FROM auth.users LIMIT 1",
            "SELECT name FROM user_profiles LIMIT 1",
            "SELECT content FROM chat_messages LIMIT 1",
            "UPDATE user_profiles SET role = 'superadmin' WHERE false",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"):
                db.execute(text(statement))
            db.rollback()
        # A commit or rollback starts a new transaction; the role comes back with it.
        assert db.execute(text("SELECT current_user")).scalar_one() == "bodify_pii_masked"
        assert db.execute(text("SELECT masked.can_read_raw()")).scalar_one() is False

    # The connection goes back to the pool as itself.
    with SessionLocal() as db:
        assert db.execute(text("SELECT current_user")).scalar_one() != "bodify_pii_masked"


@pytest.mark.parametrize("scope", ["real", "seeded"])
def test_reports_count_the_same_under_either_role(scope: str):
    today = admin_analytics.today_vn()
    window = admin_analytics.Window(today - timedelta(days=29), today, scope)
    admin_analytics._cache.clear()
    with SessionLocal() as db:
        raw = admin_analytics.overview(db, window)
    admin_analytics._cache.clear()
    with SessionLocal() as db:
        mask_personal_data(db)
        masked = admin_analytics.overview(db, window)
    admin_analytics._cache.clear()
    assert masked == raw


def _admin_get_routes() -> list[str]:
    paths = []
    for path, route in _api_routes(app.routes):
        if (isinstance(route, APIRoute) and "GET" in route.methods
                and "/admin/" in path and "{" not in path
                and "/admin/mfa" not in path):
            paths.append(path)
    return sorted(set(paths))


def test_every_admin_page_still_answers_under_the_masked_role(client: TestClient, staff: dict):
    """A table the masked role was never granted fails here, not in production."""
    paths = _admin_get_routes()
    assert len(paths) > 10, paths
    for path in paths:
        response = client.get(path, headers=staff["headers"])
        if response.status_code == 403 and response.json()["detail"].get("code") == "superadmin_required":
            continue
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text[:300]}"


def test_catalog_edits_work_under_the_masked_role(client: TestClient, staff: dict):
    name = f"pytest-pii-{re.sub('[^a-z0-9]', '', staff['id'])[:8]}"
    created = client.post(f"{API}/admin/muscle-groups", headers=staff["headers"], json={"name": name})
    assert created.status_code == 201, created.text
    mid = created.json()["muscle_id"]
    renamed = client.put(f"{API}/admin/muscle-groups/{mid}", headers=staff["headers"],
                         json={"name": name + "-x"})
    assert renamed.status_code == 200, renamed.text
    assert client.delete(f"{API}/admin/muscle-groups/{mid}", headers=staff["headers"]).status_code == 204
