"""Admin dashboard: analytics and operating costs. Read-only.

Every route requires an admin (`AdminUserDep`); hiding the page in the
browser is navigation, not authorization.
"""

from datetime import date, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies import AdminUserDep, DbSession
from app.schemas.admin_analytics import (
    ActiveUsersPoint,
    AiUsageRead,
    FeatureAdoptionRead,
    GrowthPoint,
    InfrastructureRead,
    MuscleShare,
    NutritionPoint,
    OverviewRead,
    RetentionCohort,
    TopExercise,
    WorkoutsPoint,
)
from app.services import admin_analytics as analytics
from app.services.admin_analytics import MAX_DAYS, Scope, Window

router = APIRouter(prefix="/admin", tags=["admin-analytics"])

Language = Literal["vi", "en"]


def analytics_window(
    _: AdminUserDep,
    from_: Annotated[date | None, Query(alias="from", description="First day, Vietnam time")] = None,
    to: Annotated[date | None, Query(description="Last day, inclusive. Default: today")] = None,
    scope: Annotated[Scope, Query(description="all, real (not seeded) or seeded accounts")] = "all",
) -> Window:
    """The admin check runs here too, before any query is built."""
    d_to = to or analytics.today_vn()
    d_from = from_ or d_to - timedelta(days=29)
    if d_from > d_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "`from` must not be after `to`")
    if (d_to - d_from).days + 1 > MAX_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"A range can cover at most {MAX_DAYS} days"
        )
    return Window(d_from, d_to, scope)


WindowDep = Annotated[Window, Depends(analytics_window)]


@router.get("/analytics/overview", response_model=OverviewRead, response_model_by_alias=True)
def get_overview(db: DbSession, window: WindowDep) -> dict:
    return analytics.overview(db, window)


@router.get("/analytics/growth", response_model=list[GrowthPoint])
def get_growth(db: DbSession, window: WindowDep) -> list[dict]:
    return analytics.growth(db, window)


@router.get("/analytics/active-users", response_model=list[ActiveUsersPoint])
def get_active_users(db: DbSession, window: WindowDep) -> list[dict]:
    return analytics.active_users(db, window)


@router.get("/analytics/workouts", response_model=list[WorkoutsPoint])
def get_workouts(db: DbSession, window: WindowDep) -> list[dict]:
    return analytics.workouts(db, window)


@router.get("/analytics/nutrition", response_model=list[NutritionPoint])
def get_nutrition(db: DbSession, window: WindowDep) -> list[dict]:
    return analytics.nutrition(db, window)


@router.get("/analytics/muscle-distribution", response_model=list[MuscleShare])
def get_muscle_distribution(
    db: DbSession, window: WindowDep, language: Language = "vi"
) -> list[dict]:
    return analytics.muscle_distribution(db, window, language)


@router.get("/analytics/top-exercises", response_model=list[TopExercise])
def get_top_exercises(
    db: DbSession,
    window: WindowDep,
    language: Language = "vi",
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[dict]:
    return analytics.top_exercises(db, window, language, limit)


@router.get("/analytics/retention", response_model=list[RetentionCohort])
def get_retention(
    db: DbSession,
    window: WindowDep,
    months: Annotated[int, Query(ge=2, le=24)] = 12,
) -> list[dict]:
    return analytics.retention(db, window, months)


@router.get("/analytics/feature-adoption", response_model=FeatureAdoptionRead)
def get_feature_adoption(db: DbSession, window: WindowDep) -> dict:
    return analytics.feature_adoption(db, window)


@router.get("/costs/ai-usage", response_model=AiUsageRead)
def get_ai_usage(db: DbSession, window: WindowDep) -> dict:
    return analytics.ai_usage(db, window)


@router.get("/costs/infrastructure", response_model=InfrastructureRead)
def get_infrastructure(_: AdminUserDep, db: DbSession) -> dict:
    return {"database_bytes": analytics.database_size(db)}
