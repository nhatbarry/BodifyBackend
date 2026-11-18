"""Admin console: accounts.

Read through the view `masked.accounts` (auth.users joined to
user_profiles): real email, name, avatar and date of birth for a superadmin,
masked for everyone else, decided by the database role the request runs
under (app/db/pii.py). Two writes, and neither touches GoTrue's tables directly:

- the role, which is ours (`user_profiles.role`), and the admin roles a
  person holds (`user_admin_roles`) - both for superadmins only;
- locking, which goes through GoTrue's admin API (`ban_duration`), and which
  `get_current_user` honours at once by reading `banned_until` - a ban alone
  only stops new tokens being issued.

Accounts are not created or deleted here: GoTrue owns their lifecycle, and
deleting one would orphan rows in a dozen tables that carry a bare uuid.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, or_, select, text
from supabase_auth.errors import AuthApiError, AuthError

from app.api.dependencies import STAFF_ROLES, DbSession, SuperAdminDep, requires
from app.api.endpoints.admin_roles import roles_of
from app.core.supabase_client import get_supabase_admin
from app.models.admin_roles import AdminRole, UserAdminRole
from app.models.masked_accounts import MaskedAccount
from app.models.user_profiles import UserProfile
from app.schemas.admin_roles import UserAdminRolesUpdate
from app.models.users import User
from app.schemas.admin_users import (
    AdminUserActivity,
    AdminUserDetail,
    AdminUserSummary,
    BanRequest,
    RoleUpdate,
)
from app.schemas.common import Page
from app.services.admin_common import committing, conflict, not_found
from app.services.avatar import avatar_url

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

UsersView = requires("users.view")
UsersBan = requires("users.ban")

FOREVER = "876000h"


def _is_banned():
    return func.coalesce(User.banned_until > func.now(), False)


def _to_summary(a: MaskedAccount) -> AdminUserSummary:
    return AdminUserSummary(
        user_id=a.user_id, email=a.email, name=a.name, role=a.role, created_at=a.created_at,
        last_sign_in_at=a.last_sign_in_at, banned_until=a.banned_until if a.is_banned else None,
        is_banned=a.is_banned, is_seeded=a.is_seeded, mfa_enabled=a.mfa_enabled,
        pii_masked=a.pii_masked,
    )


@router.get("", response_model=Page[AdminUserSummary])
def list_users(
    _: UsersView,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    # staff = admin or superadmin: everyone who can open the console.
    role: Literal["user", "admin", "superadmin", "staff"] | None = None,
    scope: Literal["all", "real", "seeded"] = "real",
    state: Literal["all", "active", "banned"] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[AdminUserSummary]:
    A = MaskedAccount
    conditions = []
    term = (search or "").strip()
    if term:
        # Masked, `email` and `name` hold the masked text, so a partial
        # match finds only what is already on screen. A whole address still
        # finds its account, through a lookup that returns ids alone.
        needle = f"%{term}%"
        alternatives = [
            A.email.ilike(needle),
            func.unaccent(A.name).ilike(func.unaccent(needle)),
            A.user_id.in_(select(func.masked.account_ids_by_email(term))),
        ]
        try:
            alternatives.append(A.user_id == uuid.UUID(term))
        except ValueError:
            pass
        conditions.append(or_(*alternatives))
    if role in ("admin", "superadmin", "user"):
        conditions.append(A.role == role)
    elif role == "staff":
        conditions.append(A.role.in_(STAFF_ROLES))
    if scope == "real":
        conditions.append(~A.is_seeded)
    elif scope == "seeded":
        conditions.append(A.is_seeded)
    if state == "banned":
        conditions.append(A.is_banned)
    elif state == "active":
        conditions.append(~A.is_banned)

    total = db.scalar(select(func.count()).select_from(A).where(*conditions)) or 0
    rows = db.scalars(
        select(A)
        .where(*conditions)
        .order_by(A.created_at.desc().nulls_last(), A.user_id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    items = [_to_summary(r) for r in rows]
    held = roles_of(db, [i.user_id for i in items if i.role == "admin"])
    for item in items:
        item.admin_roles = held.get(item.user_id, [])
    return Page(items=items, page=page, limit=limit, total=total)


def _detail(db, user_id: uuid.UUID) -> AdminUserDetail:
    account = db.get(MaskedAccount, user_id, populate_existing=True)
    if account is None:
        raise not_found("tài khoản")
    summary = _to_summary(account)
    summary.admin_roles = roles_of(db, [user_id]).get(user_id, [])

    activity = db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM workout_sessions WHERE user_id = :u AND status = 'completed'),
              (SELECT max(completed_at) FROM workout_sessions WHERE user_id = :u),
              (SELECT count(*) FROM meal_items i JOIN meals m ON m.meal_id = i.meal_id WHERE m.user_id = :u),
              (SELECT count(*) FROM body_metrics WHERE user_id = :u),
              (SELECT count(*) FROM chat_messages c JOIN chat_sessions s
                 ON s.chat_session_id = c.chat_session_id
               WHERE s.user_id = :u AND c.role = 'user')
            """
        ),
        {"u": user_id},
    ).one()
    avatar = None
    if account.avatar_key:  # null when masked
        try:
            avatar = avatar_url(account.avatar_key)
        except RuntimeError:  # upload bucket not configured
            avatar = None

    return AdminUserDetail(
        **summary.model_dump(),
        avatar_url=avatar,
        date_of_birth=account.date_of_birth,
        birth_year=account.birth_year,
        gender=account.gender,
        has_profile=account.has_profile,
        providers=list(account.providers or []),
        activity=AdminUserActivity(
            workouts_completed=activity[0], last_workout_at=activity[1],
            meal_items=activity[2], body_metrics=activity[3], coach_questions=activity[4],
        ),
    )


@router.get("/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: uuid.UUID, _: UsersView, db: DbSession) -> AdminUserDetail:
    return _detail(db, user_id)


def _locked_profile(db, user_id: uuid.UUID, me_id: uuid.UUID, own_message: str) -> UserProfile:
    """The target's profile, row-locked until commit, after the shared refusals."""
    if user_id == me_id:
        raise conflict(own_message)
    profile = db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
    )
    if profile is None:
        if db.get(User, user_id) is None:
            raise not_found("tài khoản")
        raise conflict("Tài khoản này chưa có hồ sơ (chưa từng mở ứng dụng)")
    return profile


def _refuse_if_banned(db, user_id: uuid.UUID) -> None:
    if db.scalar(select(_is_banned()).where(User.id == user_id)):
        raise conflict("Tài khoản đang bị khoá; hãy mở khoá trước khi cấp quyền quản trị")


@router.patch("/{user_id}/role", response_model=AdminUserDetail)
def change_role(
    user_id: uuid.UUID, payload: RoleUpdate, me: SuperAdminDep, db: DbSession
) -> AdminUserDetail:
    """Make someone a superadmin, or take all console access away.

    Either way their admin roles are dropped: a superadmin needs none, and a
    person brought back later should not silently get the old ones again.
    """
    profile = _locked_profile(
        db, user_id, me.id,
        "Không thể tự đổi vai trò của chính mình; hãy nhờ một quản trị viên cấp cao khác",
    )
    if profile.role == payload.role:
        db.rollback()
        return _detail(db, user_id)
    if payload.role == "superadmin":
        _refuse_if_banned(db, user_id)
    if profile.role == "superadmin":
        # Locked for the rest of the transaction, so two superadmins demoting
        # each other at once cannot both pass the "last one" check.
        superadmins = db.execute(
            select(UserProfile.user_id).where(UserProfile.role == "superadmin").with_for_update()
        ).scalars().all()
        if len(superadmins) <= 1:
            raise conflict("Đây là quản trị viên cấp cao cuối cùng; hãy nâng người khác trước")
    db.execute(delete(UserAdminRole).where(UserAdminRole.user_id == user_id))
    profile.role = payload.role
    profile.updated_at = func.now()
    db.commit()
    return _detail(db, user_id)


@router.put("/{user_id}/admin-roles", response_model=AdminUserDetail)
def set_admin_roles(
    user_id: uuid.UUID, payload: UserAdminRolesUpdate, me: SuperAdminDep, db: DbSession
) -> AdminUserDetail:
    """Every role the person should hold.

    Any role makes them an admin; an empty list makes them a regular user
    again. Roles they keep keep their original `assigned_at`.
    """
    wanted = set(payload.admin_role_ids)
    profile = _locked_profile(db, user_id, me.id, "Không thể tự gán vai trò cho chính mình")
    if profile.role == "superadmin":
        raise conflict(
            "Tài khoản này là quản trị viên cấp cao; hãy hạ xuống người dùng trước khi gán vai trò"
        )
    if wanted:
        _refuse_if_banned(db, user_id)
        found = set(
            db.scalars(select(AdminRole.admin_role_id).where(AdminRole.admin_role_id.in_(wanted))).all()
        )
        missing = sorted(wanted - found)
        if missing:
            raise not_found(f"vai trò {', '.join(map(str, missing))}")

    with committing(db):
        current = set(
            db.scalars(select(UserAdminRole.admin_role_id).where(UserAdminRole.user_id == user_id)).all()
        )
        if current - wanted:
            db.execute(
                delete(UserAdminRole).where(
                    UserAdminRole.user_id == user_id,
                    UserAdminRole.admin_role_id.in_(current - wanted),
                )
            )
        db.add_all(
            UserAdminRole(user_id=user_id, admin_role_id=rid, assigned_by=me.id)
            for rid in sorted(wanted - current)
        )
        new_role = "admin" if wanted else "user"
        if profile.role != new_role:
            profile.role = new_role
            profile.updated_at = func.now()
    db.expire_all()
    return _detail(db, user_id)


def _set_ban(user_id: uuid.UUID, duration: str) -> None:
    try:
        get_supabase_admin().auth.admin.update_user_by_id(str(user_id), {"ban_duration": duration})
    except (AuthApiError, AuthError) as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            getattr(exc, "message", None) or "Dịch vụ đăng nhập từ chối thay đổi",
        ) from exc


@router.post("/{user_id}/ban", response_model=AdminUserDetail)
def ban_user(
    user_id: uuid.UUID, admin: UsersBan, db: DbSession, payload: BanRequest | None = None
) -> AdminUserDetail:
    """Lock the account: no new sign-in, and its current tokens stop working."""
    if user_id == admin.id:
        raise conflict("Không thể tự khoá tài khoản của chính mình")
    current = _detail(db, user_id)
    if current.role in STAFF_ROLES:
        raise conflict("Hãy thu quyền quản trị trước khi khoá tài khoản này")
    hours = payload.hours if payload and payload.hours else None
    _set_ban(user_id, f"{hours}h" if hours else FOREVER)
    db.expire_all()
    return _detail(db, user_id)


@router.post("/{user_id}/unban", response_model=AdminUserDetail)
def unban_user(user_id: uuid.UUID, _: UsersBan, db: DbSession) -> AdminUserDetail:
    _detail(db, user_id)
    _set_ban(user_id, "none")
    db.expire_all()
    return _detail(db, user_id)
