"""Admin console: roles a superadmin defines, enforced by the API.

The console hides what a person cannot use, but these tests talk to the API
directly, the way a token copied out of the browser would. Every refusal
here is one the server makes on its own.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.permissions import ALL_PERMISSIONS, PERMISSIONS
from app.db.session import SessionLocal
from app.main import app
from tests.conftest import API
from tests.test_admin_mfa import _enroll_and_verify, _make_admin


def _bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _user_id(email: str) -> str:
    with SessionLocal() as db:
        return str(db.execute(text("SELECT id FROM auth.users WHERE email = :e"), {"e": email}).scalar_one())


@pytest.fixture(autouse=True)
def _remove_test_roles() -> Iterator[None]:
    yield
    with SessionLocal() as db:
        db.execute(text(
            "DELETE FROM user_admin_roles WHERE admin_role_id IN "
            "(SELECT admin_role_id FROM admin_roles WHERE name LIKE 'pytest-%')"
        ))
        db.execute(text("DELETE FROM admin_roles WHERE name LIKE 'pytest-%'"))
        db.commit()


@pytest.fixture
def superadmin(client: TestClient, credentials: Callable[[], dict]) -> dict:
    creds = credentials()
    _make_admin(creds["email"])
    _, _, tokens = _enroll_and_verify(client, creds["headers"])
    return {"headers": _bearer(tokens), "id": _user_id(creds["email"])}


@pytest.fixture
def make_role(client: TestClient, superadmin: dict) -> Callable[..., dict]:
    def make(*permissions: str, name: str | None = None) -> dict:
        response = client.post(f"{API}/admin/roles", headers=superadmin["headers"], json={
            "name": name or f"pytest-{uuid.uuid4().hex[:8]}", "permissions": list(permissions),
        })
        assert response.status_code == 201, response.text
        return response.json()
    return make


@pytest.fixture
def make_staff(client: TestClient, credentials: Callable[[], dict], superadmin: dict) -> Callable[..., dict]:
    """A fresh account holding `role_ids`, signed in at aal2."""
    def make(*role_ids: int) -> dict:
        creds = credentials()
        uid = _user_id(creds["email"])
        assigned = client.put(f"{API}/admin/users/{uid}/admin-roles", headers=superadmin["headers"],
                              json={"admin_role_ids": list(role_ids)})
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["role"] == "admin"
        _, _, tokens = _enroll_and_verify(client, creds["headers"])
        return {"headers": _bearer(tokens), "id": uid}
    return make


def _denied(response) -> bool:
    return response.status_code == 403 and response.json()["detail"]["code"] == "permission_denied"


# ------------------------------------------------------------------ the list itself


def test_the_table_lists_exactly_the_permissions_the_code_checks():
    with SessionLocal() as db:
        rows = db.execute(text(
            "SELECT permission_key, function_key, action, group_name, label "
            "FROM permissions ORDER BY sort_order"
        )).all()
    assert [tuple(r) for r in rows] == [
        (p.key, p.function_key, p.action, p.group_name, p.label) for p in PERMISSIONS
    ]


def _guards(dependant) -> set[str]:
    found = set()
    for dep in dependant.dependencies:
        name = getattr(dep.call, "__qualname__", "")
        if name.startswith("require_permission.") or name == "require_superadmin":
            found.add(name)
        found |= _guards(dep)
    return found


def _api_routes(routes, prefix: str = "") -> Iterator[tuple[str, APIRoute]]:
    """Every APIRoute with its full path. FastAPI 0.141 keeps an included
    router as a wrapper (`original_router`, `include_context.prefix`) rather
    than copying its routes into `app.routes`."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            inner = getattr(getattr(route, "include_context", None), "prefix", "") or ""
            yield from _api_routes(route.original_router.routes, prefix + inner)


def test_every_admin_route_checks_a_permission():
    """A new admin route without a guard would be open to every admin."""
    exempt = {f"{API}/admin/me"}
    admin_routes = [
        (path, route) for path, route in _api_routes(app.routes)
        if path.startswith(f"{API}/admin/") and not path.startswith(f"{API}/admin/mfa/")
    ]
    # Walking nothing would pass vacuously; it did once.
    assert len(admin_routes) > 40
    unguarded = [
        f"{sorted(route.methods)} {path}"
        for path, route in admin_routes
        if path not in exempt and not _guards(route.dependant)
    ]
    assert unguarded == []


# ------------------------------------------------------------------ what a role allows


def test_a_role_allows_what_it_lists_and_nothing_else(client: TestClient, make_role, make_staff):
    role = make_role("foods.update")
    # Editing implies seeing.
    assert role["permissions"] == ["foods.view", "foods.update"]
    staff = make_staff(role["admin_role_id"])
    h = staff["headers"]

    me = client.get(f"{API}/admin/me", headers=h).json()
    assert me["role"] == "admin" and me["is_superadmin"] is False
    assert me["permissions"] == ["foods.view", "foods.update"]
    assert [r["name"] for r in me["roles"]] == [role["name"]]

    assert client.get(f"{API}/admin/foods", headers=h).status_code == 200
    assert client.get(f"{API}/admin/food-categories", headers=h).status_code == 200
    assert client.get(f"{API}/admin/catalog-options", headers=h).status_code == 200
    # Past the guard: an empty body fails validation rather than permission.
    assert client.put(f"{API}/admin/foods/999999999", headers=h, json={}).status_code == 422

    for method, path in (
        ("POST", "/admin/foods"),
        ("DELETE", "/admin/foods/1"),
        ("POST", "/admin/food-categories"),
        ("GET", "/admin/users"),
        ("POST", f"/admin/users/{staff['id']}/ban"),
        ("GET", "/admin/exercises"),
        ("GET", "/admin/muscle-groups"),
        ("GET", "/admin/analytics/overview"),
        ("GET", "/admin/analytics/retention"),
        ("GET", "/admin/costs/infrastructure"),
        ("GET", "/admin/costs/simulated"),
    ):
        response = client.request(method, f"{API}{path}", headers=h, json={})
        assert _denied(response), (method, path, response.status_code, response.text)


def test_roles_add_up_and_changes_apply_on_the_next_request(
    client: TestClient, superadmin: dict, make_role, make_staff
):
    s = superadmin["headers"]
    foods = make_role("foods.view")
    users = make_role("users.view")
    staff = make_staff(foods["admin_role_id"], users["admin_role_id"])
    h = staff["headers"]
    assert client.get(f"{API}/admin/foods", headers=h).status_code == 200
    assert client.get(f"{API}/admin/users", headers=h).status_code == 200

    # Same token, role edited underneath it.
    edited = client.put(f"{API}/admin/roles/{foods['admin_role_id']}", headers=s,
                        json={"name": foods["name"], "permissions": ["costs.view"]})
    assert edited.status_code == 200 and edited.json()["permissions"] == ["costs.view"]
    assert _denied(client.get(f"{API}/admin/foods", headers=h))
    assert client.get(f"{API}/admin/costs/infrastructure", headers=h).status_code == 200

    # Taking every role away closes the console at once.
    cleared = client.put(f"{API}/admin/users/{staff['id']}/admin-roles", headers=s, json={"admin_role_ids": []})
    assert cleared.json()["role"] == "user" and cleared.json()["admin_roles"] == []
    assert client.get(f"{API}/admin/me", headers=h).status_code == 403


def test_only_a_superadmin_manages_roles(client: TestClient, make_role, make_staff, credentials):
    everything = make_role(*sorted(ALL_PERMISSIONS))
    staff = make_staff(everything["admin_role_id"])
    h = staff["headers"]
    other = _user_id(credentials()["email"])

    for method, path, body in (
        ("GET", "/admin/roles", None),
        ("GET", "/admin/permissions", None),
        ("POST", "/admin/roles", {"name": "pytest-self", "permissions": []}),
        ("PUT", f"/admin/roles/{everything['admin_role_id']}", {"name": "x", "permissions": []}),
        ("DELETE", f"/admin/roles/{everything['admin_role_id']}", None),
        ("PUT", f"/admin/users/{other}/admin-roles", {"admin_role_ids": [everything["admin_role_id"]]}),
        ("PATCH", f"/admin/users/{other}/role", {"role": "superadmin"}),
        ("PATCH", f"/admin/users/{staff['id']}/role", {"role": "superadmin"}),
    ):
        response = client.request(method, f"{API}{path}", headers=h, json=body)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["detail"]["code"] == "superadmin_required"


# ------------------------------------------------------------------ managing roles


def test_role_names_and_permissions_are_checked(client: TestClient, superadmin: dict, make_role):
    s = superadmin["headers"]
    role = make_role("dashboard.view")
    duplicate = client.post(f"{API}/admin/roles", headers=s,
                            json={"name": f"  {role['name'].upper()} ", "permissions": []})
    assert duplicate.status_code == 409 and "Đã có vai trò" in duplicate.json()["detail"]

    unknown = client.post(f"{API}/admin/roles", headers=s,
                          json={"name": "pytest-unknown", "permissions": ["foods.export"]})
    assert unknown.status_code == 422 and "foods.export" in unknown.json()["detail"]

    listed = client.get(f"{API}/admin/permissions", headers=s).json()
    assert [p["permission_key"] for p in listed] == [p.key for p in PERMISSIONS]


def test_a_role_people_hold_is_not_deleted(client: TestClient, superadmin: dict, make_role, make_staff):
    s = superadmin["headers"]
    role = make_role("foods.view")
    staff = make_staff(role["admin_role_id"])

    detail = client.get(f"{API}/admin/roles/{role['admin_role_id']}", headers=s).json()
    assert detail["member_count"] == 1 and detail["members"][0]["user_id"] == staff["id"]

    refused = client.delete(f"{API}/admin/roles/{role['admin_role_id']}", headers=s)
    assert refused.status_code == 409 and "1 người" in refused.json()["detail"]

    client.put(f"{API}/admin/users/{staff['id']}/admin-roles", headers=s, json={"admin_role_ids": []})
    assert client.delete(f"{API}/admin/roles/{role['admin_role_id']}", headers=s).status_code == 204
    assert client.get(f"{API}/admin/roles/{role['admin_role_id']}", headers=s).status_code == 404


# ------------------------------------------------------------------ assigning


def test_assigning_roles_and_superadmin_rules(
    client: TestClient, superadmin: dict, make_role, make_staff, credentials
):
    s = superadmin["headers"]
    role = make_role("users.view")
    me = superadmin["id"]

    assert client.patch(f"{API}/admin/users/{me}/role", headers=s, json={"role": "user"}).status_code == 409
    assert client.put(f"{API}/admin/users/{me}/admin-roles", headers=s,
                      json={"admin_role_ids": [role["admin_role_id"]]}).status_code == 409
    missing = client.put(f"{API}/admin/users/{_user_id(credentials()['email'])}/admin-roles", headers=s,
                         json={"admin_role_ids": [2147483000]})
    assert missing.status_code == 404

    staff = make_staff(role["admin_role_id"])
    listed = client.get(f"{API}/admin/users", headers=s, params={"role": "admin", "search": staff["id"]}).json()
    assert [r["name"] for r in listed["items"][0]["admin_roles"]] == [role["name"]]
    # Staff are not locked out from the user list; take their access first.
    assert client.post(f"{API}/admin/users/{staff['id']}/ban", headers=s, json={}).status_code == 409

    promoted = client.patch(f"{API}/admin/users/{staff['id']}/role", headers=s, json={"role": "superadmin"})
    assert promoted.json()["role"] == "superadmin" and promoted.json()["admin_roles"] == []
    assert client.get(f"{API}/admin/roles", headers=staff["headers"]).status_code == 200
    assert client.put(f"{API}/admin/users/{staff['id']}/admin-roles", headers=s,
                      json={"admin_role_ids": [role["admin_role_id"]]}).status_code == 409

    demoted = client.patch(f"{API}/admin/users/{staff['id']}/role", headers=s, json={"role": "user"})
    assert demoted.json()["role"] == "user"
    assert client.get(f"{API}/admin/me", headers=staff["headers"]).status_code == 403
