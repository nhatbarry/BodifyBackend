"""Admin console: accounts.

Read from `auth.users` (read-only, as everywhere) joined to `user_profiles`.
Two writes, and neither touches GoTrue's tables directly:

- the role, which is ours (`user_profiles.role`);
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
from sqlalchemy import func, literal_column, or_, select, text
from supabase_auth.errors import AuthApiError, AuthError

from app.api.dependencies import AdminUserDep, DbSession
from app.core.supabase_client import get_supabase_admin
from app.models.user_profiles import UserProfile
from app.models.users import User
from app.schemas.admin_users import (
    AdminUserActivity,
    AdminUserDetail,
    AdminUserSummary,
    BanRequest,
    RoleUpdate,
)
from app.schemas.common import Page
from app.services.admin_common import conflict, not_found
from app.services.avatar import avatar_url

router = APIRouter(prefix="/admin/users", tags=["admin-users"])

SEED_DOMAIN = "%@seed.bodify.example"
FOREVER = "876000h"


def _mfa_enabled():
    # Correlated on the outer auth.users row; GoTrue's table, read-only.
    return literal_column(
        "EXISTS (SELECT 1 FROM auth.mfa_factors f "
        "WHERE f.user_id = auth.users.id AND f.status = 'verified')"
    )


def _is_banned():
    return func.coalesce(User.banned_until > func.now(), False)


def _summary_columns():
    return (
        User.id, User.email, UserProfile.name, func.coalesce(UserProfile.role, "user"),
        User.created_at, User.last_sign_in_at, User.banned_until, _is_banned(),
        User.email.like(SEED_DOMAIN), _mfa_enabled(),
    )


def _to_summary(row) -> AdminUserSummary:
    uid, email, name, role, created, last_sign_in, banned_until, banned, seeded, mfa = row
    return AdminUserSummary(
        user_id=uid, email=email, name=name, role=role, created_at=created,
        last_sign_in_at=last_sign_in, banned_until=banned_until if banned else None,
        is_banned=bool(banned), is_seeded=bool(seeded), mfa_enabled=bool(mfa),
    )


@router.get("", response_model=Page[AdminUserSummary])
def list_users(
    _: AdminUserDep,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    role: Literal["user", "admin"] | None = None,
    scope: Literal["all", "real", "seeded"] = "real",
    state: Literal["all", "active", "banned"] = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[AdminUserSummary]:
    conditions = []
    term = (search or "").strip()
    if term:
        needle = f"%{term}%"
        alternatives = [User.email.ilike(needle), func.unaccent(UserProfile.name).ilike(func.unaccent(needle))]
        try:
            alternatives.append(User.id == uuid.UUID(term))
        except ValueError:
            pass
        conditions.append(or_(*alternatives))
    if role == "admin":
        conditions.append(UserProfile.role == "admin")
    elif role == "user":
        conditions.append(or_(UserProfile.role.is_(None), UserProfile.role == "user"))
    if scope == "real":
        conditions.append(or_(User.email.is_(None), ~User.email.like(SEED_DOMAIN)))
    elif scope == "seeded":
        conditions.append(User.email.like(SEED_DOMAIN))
    if state == "banned":
        conditions.append(_is_banned())
    elif state == "active":
        conditions.append(~_is_banned())

    base = select(User).outerjoin(UserProfile, UserProfile.user_id == User.id).where(*conditions)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.execute(
        select(*_summary_columns())
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(*conditions)
        .order_by(User.created_at.desc().nulls_last(), User.id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    return Page(items=[_to_summary(r) for r in rows], page=page, limit=limit, total=total)


def _detail(db, user_id: uuid.UUID) -> AdminUserDetail:
    row = db.execute(
        select(*_summary_columns(), UserProfile)
        .outerjoin(UserProfile, UserProfile.user_id == User.id)
        .where(User.id == user_id)
    ).first()
    if row is None:
        raise not_found("tài khoản")
    summary = _to_summary(row[:-1])
    profile: UserProfile | None = row[-1]

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
    providers = db.execute(
        text("SELECT DISTINCT provider FROM auth.identities WHERE user_id = :u ORDER BY provider"),
        {"u": user_id},
    ).scalars().all()

    avatar = None
    if profile and profile.avatar_url:
        try:
            avatar = avatar_url(profile.avatar_url)
        except RuntimeError:  # upload bucket not configured
            avatar = None

    return AdminUserDetail(
        **summary.model_dump(),
        avatar_url=avatar,
        date_of_birth=profile.date_of_birth if profile else None,
        gender=profile.gender if profile else None,
        has_profile=profile is not None,
        providers=list(providers),
        activity=AdminUserActivity(
            workouts_completed=activity[0], last_workout_at=activity[1],
            meal_items=activity[2], body_metrics=activity[3], coach_questions=activity[4],
        ),
    )


@router.get("/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: uuid.UUID, _: AdminUserDep, db: DbSession) -> AdminUserDetail:
    return _detail(db, user_id)


@router.patch("/{user_id}/role", response_model=AdminUserDetail)
def change_role(
    user_id: uuid.UUID, payload: RoleUpdate, admin: AdminUserDep, db: DbSession
) -> AdminUserDetail:
    if user_id == admin.id:
        raise conflict("Không thể tự đổi vai trò của chính mình; hãy nhờ một quản trị viên khác")
    # Locked for the rest of the transaction, so two admins demoting each
    # other at once cannot both pass the "last admin" check.
    profile = db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
    )
    if profile is None:
        if db.get(User, user_id) is None:
            raise not_found("tài khoản")
        raise conflict("Tài khoản này chưa có hồ sơ (chưa từng mở ứng dụng)")
    if profile.role == payload.role:
        db.rollback()
        return _detail(db, user_id)
    if profile.role == "admin":
        admins = db.execute(
            select(UserProfile.user_id).where(UserProfile.role == "admin").with_for_update()
        ).scalars().all()
        if len(admins) <= 1:
            raise conflict("Đây là quản trị viên cuối cùng; hãy cấp quyền cho người khác trước")
    profile.role = payload.role
    profile.updated_at = func.now()
    db.commit()
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
    user_id: uuid.UUID, admin: AdminUserDep, db: DbSession, payload: BanRequest | None = None
) -> AdminUserDetail:
    """Lock the account: no new sign-in, and its current tokens stop working."""
    if user_id == admin.id:
        raise conflict("Không thể tự khoá tài khoản của chính mình")
    current = _detail(db, user_id)
    if current.role == "admin":
        raise conflict("Hãy thu quyền quản trị trước khi khoá tài khoản này")
    hours = payload.hours if payload and payload.hours else None
    _set_ban(user_id, f"{hours}h" if hours else FOREVER)
    db.expire_all()
    return _detail(db, user_id)


@router.post("/{user_id}/unban", response_model=AdminUserDetail)
def unban_user(user_id: uuid.UUID, _: AdminUserDep, db: DbSession) -> AdminUserDetail:
    _detail(db, user_id)
    _set_ban(user_id, "none")
    db.expire_all()
    return _detail(db, user_id)
