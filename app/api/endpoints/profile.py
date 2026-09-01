from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.dependencies import CurrentUser, CurrentUserDep, DbSession
from app.models.body_metrics import BodyMetric
from app.models.user_profiles import UserProfile
from app.schemas.profile import ProfileRead, ProfileUpdate

router = APIRouter(prefix="/profile", tags=["profile"])


def _latest_metric(db, user_id) -> BodyMetric | None:
    return db.execute(
        select(BodyMetric)
        .where(BodyMetric.user_id == user_id)
        .order_by(BodyMetric.recorded_at.desc(), BodyMetric.metric_id.desc())
        .limit(1)
    ).scalar_one_or_none()


def _to_read(profile: UserProfile, current_user: CurrentUser, db) -> ProfileRead:
    metric = _latest_metric(db, current_user.id)
    return ProfileRead(
        user_id=profile.user_id,
        profile_id=profile.profile_id,
        name=profile.name,
        avatar_url=profile.avatar_url,
        date_of_birth=profile.date_of_birth,
        gender=profile.gender,
        role=profile.role,
        email=current_user.email,
        height_cm=metric.height_cm if metric else None,
        weight_kg=metric.weight_kg if metric else None,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


def _require_profile(current_user: CurrentUser) -> UserProfile:
    if current_user.profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found"
        )
    return current_user.profile


@router.get("", response_model=ProfileRead)
def get_profile(current_user: CurrentUserDep, db: DbSession) -> ProfileRead:
    return _to_read(_require_profile(current_user), current_user, db)


@router.put("", response_model=ProfileRead)
def update_profile(
    payload: ProfileUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ProfileRead:
    profile = _require_profile(current_user)

    # exclude_unset: an omitted field is left alone, while an explicit null
    # clears it.
    changes = payload.model_dump(exclude_unset=True)
    if changes:
        for field, value in changes.items():
            setattr(profile, field, value)
        db.commit()
        db.refresh(profile)

    return _to_read(profile, current_user, db)
