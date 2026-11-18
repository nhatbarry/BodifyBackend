"""Admin console: roles a superadmin defines, and the caller's own access.

A role is a name and a set of permissions from the fixed list in
`app/core/permissions.py`. People hold any number of roles and may do the
union of what they allow (`user_admin_roles`, assigned in admin_users.py).

Everything here but `/admin/me` is for superadmins only: whoever could edit
roles could add every permission to their own.
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import AdminUserDep, DbSession, SuperAdminDep
from app.core.permissions import ALL_PERMISSIONS, PERMISSIONS, with_implied_view
from app.models.admin_roles import AdminRole, AdminRolePermission, Permission, UserAdminRole
from app.models.user_profiles import UserProfile
from app.models.users import User
from app.schemas.admin_roles import (
    AdminMe,
    AdminRoleDetail,
    AdminRoleMember,
    AdminRoleRead,
    AdminRoleRef,
    AdminRoleWrite,
    PermissionRead,
)
from app.services.admin_common import committing, conflict, not_found

router = APIRouter(prefix="/admin", tags=["admin-roles"])

_ORDER = {p.key: i for i, p in enumerate(PERMISSIONS)}


def ordered(keys) -> list[str]:
    return sorted(keys, key=lambda k: _ORDER.get(k, len(_ORDER)))


def roles_of(db: Session, user_ids: list) -> dict:
    """user_id -> [AdminRoleRef], for a page of people in one query."""
    grouped: dict = defaultdict(list)
    if not user_ids:
        return grouped
    rows = db.execute(
        select(UserAdminRole.user_id, AdminRole.admin_role_id, AdminRole.name)
        .join(AdminRole, AdminRole.admin_role_id == UserAdminRole.admin_role_id)
        .where(UserAdminRole.user_id.in_(user_ids))
        .order_by(func.lower(AdminRole.name))
    ).all()
    for user_id, role_id, name in rows:
        grouped[user_id].append(AdminRoleRef(admin_role_id=role_id, name=name))
    return grouped


@router.get("/me", response_model=AdminMe)
def admin_me(me: AdminUserDep, db: DbSession) -> AdminMe:
    """The caller's roles and permissions. The console hides what is not
    here; the API refuses it regardless."""
    return AdminMe(
        user_id=me.id,
        email=me.email,
        role=me.role,
        is_superadmin=me.is_superadmin,
        roles=roles_of(db, [me.id]).get(me.id, []),
        permissions=ordered(me.permissions or ()),
    )


@router.get("/permissions", response_model=list[PermissionRead])
def list_permissions(_: SuperAdminDep, db: DbSession) -> list[PermissionRead]:
    rows = db.scalars(select(Permission).order_by(Permission.sort_order, Permission.permission_key)).all()
    return [
        PermissionRead(
            permission_key=p.permission_key, function_key=p.function_key, action=p.action,
            group_name=p.group_name, label=p.label,
        )
        for p in rows
    ]


# ---------------------------------------------------------------- roles


def _permissions_by_role(db: Session, role_ids: list[int]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = defaultdict(list)
    if role_ids:
        for role_id, key in db.execute(
            select(AdminRolePermission.admin_role_id, AdminRolePermission.permission_key)
            .where(AdminRolePermission.admin_role_id.in_(role_ids))
        ).all():
            grouped[role_id].append(key)
    return {rid: ordered(keys) for rid, keys in grouped.items()}


def _member_counts(db: Session, role_ids: list[int]) -> dict[int, int]:
    if not role_ids:
        return {}
    return dict(
        db.execute(
            select(UserAdminRole.admin_role_id, func.count())
            .where(UserAdminRole.admin_role_id.in_(role_ids))
            .group_by(UserAdminRole.admin_role_id)
        ).all()
    )


def _read(role: AdminRole, permissions: list[str], members: int) -> AdminRoleRead:
    return AdminRoleRead(
        admin_role_id=role.admin_role_id, name=role.name, description=role.description,
        permissions=permissions, member_count=members,
        created_at=role.created_at, updated_at=role.updated_at,
    )


def _detail(db: Session, role_id: int) -> AdminRoleDetail:
    role = db.get(AdminRole, role_id)
    if role is None:
        raise not_found("vai trò")
    members = db.execute(
        select(User.id, User.email, UserProfile.name)
        .join(UserAdminRole, UserAdminRole.user_id == User.id)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(UserAdminRole.admin_role_id == role_id)
        .order_by(User.email)
    ).all()
    base = _read(role, _permissions_by_role(db, [role_id]).get(role_id, []), len(members))
    return AdminRoleDetail(
        **base.model_dump(),
        members=[AdminRoleMember(user_id=u, email=e, name=n) for u, e, n in members],
    )


def _validated_permissions(keys: list[str]) -> set[str]:
    unknown = sorted(set(keys) - ALL_PERMISSIONS)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Quyền không tồn tại: {', '.join(unknown)}"
        )
    return with_implied_view(set(keys))


def _write_permissions(db: Session, role_id: int, keys: set[str]) -> None:
    db.execute(delete(AdminRolePermission).where(AdminRolePermission.admin_role_id == role_id))
    db.add_all(AdminRolePermission(admin_role_id=role_id, permission_key=k) for k in keys)


@router.get("/roles", response_model=list[AdminRoleRead])
def list_roles(_: SuperAdminDep, db: DbSession) -> list[AdminRoleRead]:
    roles = db.scalars(select(AdminRole).order_by(func.lower(AdminRole.name))).all()
    ids = [r.admin_role_id for r in roles]
    permissions = _permissions_by_role(db, ids)
    counts = _member_counts(db, ids)
    return [_read(r, permissions.get(r.admin_role_id, []), counts.get(r.admin_role_id, 0)) for r in roles]


@router.get("/roles/{role_id}", response_model=AdminRoleDetail)
def get_role(role_id: int, _: SuperAdminDep, db: DbSession) -> AdminRoleDetail:
    return _detail(db, role_id)


@router.post("/roles", response_model=AdminRoleDetail, status_code=status.HTTP_201_CREATED)
def create_role(payload: AdminRoleWrite, me: SuperAdminDep, db: DbSession) -> AdminRoleDetail:
    keys = _validated_permissions(payload.permissions)
    with committing(db):
        role = AdminRole(name=payload.name, description=payload.description, created_by=me.id)
        db.add(role)
        db.flush()
        _write_permissions(db, role.admin_role_id, keys)
    return _detail(db, role.admin_role_id)


@router.put("/roles/{role_id}", response_model=AdminRoleDetail)
def update_role(role_id: int, payload: AdminRoleWrite, _: SuperAdminDep, db: DbSession) -> AdminRoleDetail:
    """Applies to everyone holding the role from their next request."""
    keys = _validated_permissions(payload.permissions)
    with committing(db):
        role = db.scalar(select(AdminRole).where(AdminRole.admin_role_id == role_id).with_for_update())
        if role is None:
            raise not_found("vai trò")
        role.name = payload.name
        role.description = payload.description
        role.updated_at = func.now()
        _write_permissions(db, role_id, keys)
    db.expire_all()
    return _detail(db, role_id)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: int, _: SuperAdminDep, db: DbSession) -> Response:
    """Refused while anyone holds it: their access would vanish unannounced.

    The row lock makes a concurrent assignment wait for this transaction,
    and the RESTRICT foreign key then refuses it.
    """
    with committing(db):
        role = db.scalar(select(AdminRole).where(AdminRole.admin_role_id == role_id).with_for_update())
        if role is None:
            raise not_found("vai trò")
        members = _member_counts(db, [role_id]).get(role_id, 0)
        if members:
            raise conflict(f"Vai trò đang được gán cho {members} người; hãy gỡ khỏi họ trước khi xoá")
        db.delete(role)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
