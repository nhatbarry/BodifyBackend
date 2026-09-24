"""The daily target, and what the diary adds up to against it.

Separate from `meals.py` because these answer a different question: that file
records what was eaten, this one reads it back. Nothing here stores a total -
every figure is summed from `meal_items` on read, for the same reason BMI is
computed rather than stored.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.meals import Meal, MealItem
from app.models.nutrition_targets import NutritionTarget
from app.schemas.meals import DaySummary, NutritionTargetRead, NutritionTargetUpsert

router = APIRouter(tags=["nutrition"])

# A year of history in one request is enough for any chart the app draws, and
# stops a caller asking for a decade of rows by accident.
MAX_RANGE_DAYS = 366


@router.get("/nutrition-targets", response_model=NutritionTargetRead)
def get_target(current_user: CurrentUserDep, db: DbSession) -> NutritionTargetRead:
    target = db.scalar(
        select(NutritionTarget).where(NutritionTarget.user_id == current_user.id)
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No nutrition target set"
        )
    return NutritionTargetRead.model_validate(target)


@router.put("/nutrition-targets", response_model=NutritionTargetRead)
def set_target(
    payload: NutritionTargetUpsert,
    current_user: CurrentUserDep,
    db: DbSession,
) -> NutritionTargetRead:
    """Set or replace the daily target.

    One statement rather than select-then-insert: two devices saving at once
    would otherwise both find no row and both insert, and the unique key would
    fail the second one with an error the user did nothing to deserve.
    """
    statement = (
        insert(NutritionTarget)
        .values(user_id=current_user.id, **payload.model_dump())
        .on_conflict_do_update(
            index_elements=[NutritionTarget.user_id],
            set_={**payload.model_dump(), "updated_at": func.now()},
        )
        .returning(NutritionTarget)
    )
    target = db.scalars(statement).one()
    db.commit()
    return NutritionTargetRead.model_validate(target)


@router.delete("/nutrition-targets", status_code=status.HTTP_204_NO_CONTENT)
def clear_target(current_user: CurrentUserDep, db: DbSession):
    """Stop tracking against a target. The diary still records everything."""
    db.execute(
        NutritionTarget.__table__.delete().where(
            NutritionTarget.user_id == current_user.id
        )
    )
    db.commit()


@router.get("/nutrition/summary", response_model=list[DaySummary])
def daily_summary(
    current_user: CurrentUserDep,
    db: DbSession,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
) -> list[DaySummary]:
    """Daily totals across a range, for the history strip and any chart.

    Aggregated in the database rather than by pulling every item and adding
    them up in Python - a month of diary is hundreds of rows the client has no
    other use for. Days with nothing logged are absent rather than zero: a day
    the user did not track is not a day they ate nothing.
    """
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must not be before 'from'",
        )
    if date_to - date_from > timedelta(days=MAX_RANGE_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Range must not exceed {MAX_RANGE_DAYS} days",
        )

    zero = func.coalesce
    rows = db.execute(
        select(
            Meal.logged_date,
            zero(func.sum(MealItem.energy_kcal), 0).label("energy_kcal"),
            zero(func.sum(MealItem.protein_g), 0).label("protein_g"),
            zero(func.sum(MealItem.fat_g), 0).label("fat_g"),
            zero(func.sum(MealItem.carb_g), 0).label("carb_g"),
            zero(func.sum(MealItem.fiber_g), 0).label("fiber_g"),
            func.count(func.distinct(Meal.meal_id)).label("meal_count"),
        )
        .join(MealItem, MealItem.meal_id == Meal.meal_id)
        .where(
            Meal.user_id == current_user.id,
            Meal.logged_date >= date_from,
            Meal.logged_date <= date_to,
        )
        .group_by(Meal.logged_date)
        .order_by(Meal.logged_date)
    ).all()

    return [DaySummary(**row._mapping) for row in rows]
