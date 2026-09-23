"""The food diary.

A meal is addressed by (date, type), never by id: the user taps "add to
lunch", and there is exactly one lunch on any given day for the food to land
in. So `POST /meal-items` finds or creates that row - making the client do a
create-meal-then-add-item dance would be two requests that can fail halfway
and strand an empty lunch in somebody's diary.

Ownership is folded into every WHERE clause, so another user's meal answers
404 rather than 403 - the same rule the rest of this API follows.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.foods import Food
from app.models.meals import Meal, MealItem
from app.models.nutrition_targets import NutritionTarget
from app.schemas.meals import (
    DayRead,
    MealItemCreate,
    MealItemRead,
    MealItemUpdate,
    MealRead,
    MealUpdate,
    NutritionTargetRead,
    Totals,
)
from app.services.nutrition import MACRO_FIELDS, portion

router = APIRouter(tags=["meals"])

# The order the diary screen renders them in, which is not alphabetical.
MEAL_ORDER = ("breakfast", "lunch", "dinner", "snack")


def _totals(items: list[MealItem]) -> Totals:
    """Summed on read. A macro nobody published counts as nothing rather than
    poisoning the total with a None."""
    result = Totals()
    for item in items:
        result.energy_kcal += item.energy_kcal or Decimal("0")
        for field in MACRO_FIELDS:
            value = getattr(item, field, None)
            if value is not None:
                setattr(result, field, getattr(result, field) + value)
    return result


def _to_read(meal: Meal) -> MealRead:
    result = MealRead.model_validate(meal)
    result.items = [MealItemRead.model_validate(i) for i in meal.items]
    result.totals = _totals(list(meal.items))
    return result


def _owned_meal(db: Session, meal_id: int, user_id: uuid.UUID) -> Meal:
    meal = db.scalar(
        select(Meal)
        .options(selectinload(Meal.items))
        .where(Meal.meal_id == meal_id, Meal.user_id == user_id)
    )
    if meal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meal not found"
        )
    return meal


def _owned_item(db: Session, item_id: int, user_id: uuid.UUID) -> MealItem:
    item = db.scalar(
        select(MealItem)
        .join(Meal, Meal.meal_id == MealItem.meal_id)
        .where(MealItem.meal_item_id == item_id, Meal.user_id == user_id)
    )
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Meal item not found"
        )
    return item


def _food_name(food: Food) -> str:
    for translation in food.translations:
        if translation.language_code == "vi":
            return translation.name
    return food.translations[0].name if food.translations else f"Food {food.food_id}"


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------


@router.post("/meal-items", response_model=MealItemRead, status_code=status.HTTP_201_CREATED)
def log_meal_item(
    payload: MealItemCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Log one food into a meal, creating the meal if it is the day's first.

    The nutrition is computed here and **written into the row**, not looked up
    on read. Correcting the catalog next month must not rewrite whether the
    user hit their target today.
    """
    food = db.scalar(
        select(Food)
        .options(selectinload(Food.translations))
        .where(Food.food_id == payload.food_id)
    )
    if food is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Food not found"
        )

    # Raises 422 naming the reason when the unit does not suit the food.
    values = portion(food, payload.quantity, payload.unit)

    meal = db.scalar(
        select(Meal).where(
            Meal.user_id == current_user.id,
            Meal.logged_date == payload.logged_date,
            Meal.meal_type == payload.meal_type,
        )
    )
    if meal is None:
        meal = Meal(
            user_id=current_user.id,
            logged_date=payload.logged_date,
            meal_type=payload.meal_type,
        )
        db.add(meal)
        db.flush()

    next_order = db.scalar(
        select(func.coalesce(func.max(MealItem.sort_order), -1) + 1).where(
            MealItem.meal_id == meal.meal_id
        )
    )

    item = MealItem(
        meal_id=meal.meal_id,
        food_id=food.food_id,
        food_name=_food_name(food)[:200],
        quantity=payload.quantity,
        unit=payload.unit,
        sort_order=next_order or 0,
        **values,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return MealItemRead.model_validate(item)


@router.put("/meal-items/{item_id}", response_model=MealItemRead)
def update_meal_item(
    item_id: int,
    payload: MealItemUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Correct an amount.

    The nutrition is recomputed from **today's** catalog figures, because the
    user is restating what they ate rather than asking to keep an old number.
    A row whose food has since been removed from the catalog cannot be
    recomputed at all, and says so instead of guessing.
    """
    item = _owned_item(db, item_id, current_user.id)

    if item.food_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This entry's food is no longer in the catalog, so the amount "
                "cannot be recalculated. Delete it and log the food again."
            ),
        )

    food = db.scalar(
        select(Food)
        .options(selectinload(Food.translations))
        .where(Food.food_id == item.food_id)
    )
    values = portion(food, payload.quantity, payload.unit)

    item.quantity = payload.quantity
    item.unit = payload.unit
    for field, value in values.items():
        setattr(item, field, value)
    item.updated_at = func.now()

    db.commit()
    db.refresh(item)
    return MealItemRead.model_validate(item)


@router.delete("/meal-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meal_item(
    item_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Remove one food.

    Removing the last food in a sitting removes the sitting too: an empty
    lunch is not something the user logged, and it would render as a heading
    with nothing under it.
    """
    item = _owned_item(db, item_id, current_user.id)
    meal_id = item.meal_id

    db.delete(item)
    db.flush()

    remaining = db.scalar(
        select(func.count()).select_from(MealItem).where(MealItem.meal_id == meal_id)
    )
    if not remaining:
        meal = db.get(Meal, meal_id)
        # A note or a photo is the user's own writing, so a sitting that has
        # one is kept even with nothing left in it.
        if meal is not None and not meal.note and not meal.photo_key:
            db.delete(meal)

    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Reading the diary
# --------------------------------------------------------------------------


@router.get("/meals", response_model=DayRead)
def get_day(
    current_user: CurrentUserDep,
    db: DbSession,
    logged_date: date = Query(..., alias="date", description="The day to read"),
) -> DayRead:
    """One day: every sitting, the day's totals, and the target to compare
    against.

    Sittings with nothing in them are simply absent, so an untouched day comes
    back with an empty list rather than four empty meals.
    """
    meals = db.scalars(
        select(Meal)
        .options(selectinload(Meal.items))
        .where(Meal.user_id == current_user.id, Meal.logged_date == logged_date)
    ).all()

    ordered = sorted(
        meals,
        key=lambda m: MEAL_ORDER.index(m.meal_type)
        if m.meal_type in MEAL_ORDER
        else len(MEAL_ORDER),
    )

    every_item = [item for meal in ordered for item in meal.items]
    totals = _totals(every_item)

    target = db.scalar(
        select(NutritionTarget).where(NutritionTarget.user_id == current_user.id)
    )

    return DayRead(
        logged_date=logged_date,
        meals=[_to_read(meal) for meal in ordered],
        totals=totals,
        target=NutritionTargetRead.model_validate(target) if target else None,
        # None rather than 0 when no target is set: "you have none left" and
        # "you never said what you were aiming for" are different answers.
        remaining_kcal=(target.energy_kcal - totals.energy_kcal) if target else None,
    )


@router.put("/meals/{meal_id}", response_model=MealRead)
def update_meal(
    meal_id: int,
    payload: MealUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealRead:
    """Set the note or photo on a sitting."""
    meal = _owned_meal(db, meal_id, current_user.id)

    fields = payload.model_dump(exclude_unset=True)
    for field, value in fields.items():
        setattr(meal, field, value)
    meal.updated_at = func.now()

    db.commit()
    db.refresh(meal)
    return _to_read(meal)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meal(
    meal_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Remove a whole sitting and everything logged in it."""
    meal = _owned_meal(db, meal_id, current_user.id)
    db.delete(meal)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
