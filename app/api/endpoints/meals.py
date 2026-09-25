"""The food diary.

A day holds any number of meals, each named by the user (or not - a meal with
no name shows as "Bữa 1", "Bữa 2" by position). The user creates a meal, then
puts foods in it, so `POST /meals` exists and an empty meal is legitimate: it
is what somebody planning tomorrow has before they choose what goes in it.

Each food is either **planned** or **eaten**. A planned item is ticked when it
is eaten, optionally with a different amount; the plan is kept next to the
actual so the two can be compared. Totals and the comparison with the target
count what was eaten.

Ownership is folded into every WHERE clause, so another user's meal answers
404 rather than 403 - the same rule the rest of this API follows.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.dependencies import CurrentUserDep, DbSession
from app.api.endpoints.foods import DEFAULT_LANG, _component_names, _name_for, _to_summary
from app.models.foods import Food, FoodCategory
from app.models.meals import STATUS_EATEN, STATUS_PLANNED, UNIT_SERVING, Meal, MealItem
from app.models.nutrition_targets import NutritionTarget
from app.schemas.meals import (
    CalendarDay,
    CustomFood,
    DayRead,
    MealCreate,
    MealItemCreate,
    MealItemCustomCreate,
    MealItemRead,
    MealItemStatusUpdate,
    MealItemUpdate,
    MealRead,
    MealUpdate,
    NutritionTargetRead,
    RecentFood,
    Totals,
)
from app.services.nutrition import MACRO_FIELDS, portion

router = APIRouter(tags=["meals"])


def _eaten(items: list[MealItem]) -> list[MealItem]:
    return [item for item in items if item.status == STATUS_EATEN]


def _totals(items: list[MealItem]) -> Totals:
    """Summed on read, over what was eaten. A macro nobody published counts as
    nothing rather than poisoning the total with a None."""
    result = Totals()
    for item in _eaten(items):
        result.energy_kcal += item.energy_kcal or Decimal("0")
        for field in MACRO_FIELDS:
            value = getattr(item, field, None)
            if value is not None:
                setattr(result, field, getattr(result, field) + value)
    return result


def _planned_kcal(items: list[MealItem]) -> Decimal:
    """What the plan said these items would come to.

    Read from the snapshot rather than from `energy_kcal`, so a planned item
    that was then eaten at a different amount still counts for what was
    planned. An item that was never planned contributes nothing.
    """
    return sum(
        (i.planned_energy_kcal for i in items if i.planned_energy_kcal is not None),
        Decimal("0"),
    )


def _to_read(meal: Meal) -> MealRead:
    result = MealRead.model_validate(meal)
    result.items = [MealItemRead.model_validate(i) for i in meal.items]
    result.totals = _totals(list(meal.items))
    result.planned_kcal = _planned_kcal(list(meal.items))
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


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _restate(db: Session, item: MealItem, quantity: Decimal, unit: str) -> None:
    """Change an item's amount and every figure frozen with it.

    A catalog item is recomputed from **today's** catalog row, because the user
    is restating an amount rather than asking to keep an old number. A
    hand-typed item has no catalog row, so it scales from its own figures -
    they were entered per serving, so the ratio is exact. A catalog item whose
    food has since been removed can do neither, and says so instead of
    guessing.
    """
    if item.is_custom:
        if unit != UNIT_SERVING:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A food typed in by hand is counted in servings.",
            )
        ratio = quantity / item.quantity
        item.energy_kcal = _cents(item.energy_kcal * ratio)
        for field in MACRO_FIELDS:
            value = getattr(item, field)
            if value is not None:
                setattr(item, field, _cents(value * ratio))
        item.quantity = quantity
        return

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
    values = portion(food, quantity, unit)
    item.quantity = quantity
    item.unit = unit
    for field, value in values.items():
        setattr(item, field, value)


def _append_item(db: Session, meal: Meal, item: MealItem) -> MealItemRead:
    """Put an item at the end of its meal and return it as read back."""
    item.sort_order = db.scalar(
        select(func.coalesce(func.max(MealItem.sort_order), -1) + 1).where(
            MealItem.meal_id == meal.meal_id
        )
    ) or 0
    if item.status == STATUS_PLANNED:
        # The plan is the amount as it was planned; kept so that eating a
        # different amount later leaves it on record.
        item.planned_quantity = item.quantity
        item.planned_unit = item.unit
        item.planned_energy_kcal = item.energy_kcal
    db.add(item)
    db.commit()
    db.refresh(item)
    return MealItemRead.model_validate(item)


# --------------------------------------------------------------------------
# Meals
# --------------------------------------------------------------------------


@router.post("/meals", response_model=MealRead, status_code=status.HTTP_201_CREATED)
def create_meal(
    payload: MealCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealRead:
    """Add a meal to a day, after the ones already there.

    No lock on the position: two devices adding at once can pick the same
    number, and reads break the tie by id, so the cost is a harmless tie
    rather than a unique-key error the user did nothing to deserve.
    """
    next_position = db.scalar(
        select(func.coalesce(func.max(Meal.position), -1) + 1).where(
            Meal.user_id == current_user.id,
            Meal.logged_date == payload.logged_date,
        )
    )
    meal = Meal(
        user_id=current_user.id,
        logged_date=payload.logged_date,
        name=payload.name,
        position=next_position or 0,
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return _to_read(meal)


# A month grid is at most six weeks; anything wider is not a calendar.
CALENDAR_MAX_DAYS = 62


@router.get("/meals/calendar", response_model=list[CalendarDay])
def meal_calendar(
    current_user: CurrentUserDep,
    db: DbSession,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
) -> list[CalendarDay]:
    """Per-date totals for the diary's month calendar.

    Separate from `/nutrition/summary` on purpose: that one is the record and
    counts only what was eaten, so a planned week leaves it empty. A calendar
    has to show the plan too - which future dates already have meals lined
    up - so this returns both, side by side. `planned_kcal` reads the plan
    snapshot, the same as the diary's own `planned_kcal`.
    """
    if date_to < date_from:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must not be before 'from'",
        )
    if (date_to - date_from).days > CALENDAR_MAX_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Range must not exceed {CALENDAR_MAX_DAYS} days",
        )

    eaten = MealItem.status == STATUS_EATEN
    rows = db.execute(
        select(
            Meal.logged_date,
            func.coalesce(func.sum(MealItem.energy_kcal).filter(eaten), 0),
            func.coalesce(func.sum(MealItem.planned_energy_kcal), 0),
            func.count().filter(eaten),
            func.count().filter(MealItem.status == STATUS_PLANNED),
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

    return [
        CalendarDay(
            logged_date=day,
            eaten_kcal=eaten_kcal,
            planned_kcal=planned_kcal,
            eaten_count=eaten_count,
            planned_count=planned_count,
        )
        for day, eaten_kcal, planned_kcal, eaten_count, planned_count in rows
    ]


@router.get("/meals", response_model=DayRead)
def get_day(
    current_user: CurrentUserDep,
    db: DbSession,
    logged_date: date = Query(..., alias="date", description="The day to read"),
) -> DayRead:
    """One day: every meal in order, what was eaten, what was planned, and the
    target to compare against.

    Works for any date, the future included - that is how a plan is read.
    An untouched day comes back with an empty list of meals.
    """
    meals = db.scalars(
        select(Meal)
        .options(selectinload(Meal.items))
        .where(Meal.user_id == current_user.id, Meal.logged_date == logged_date)
        .order_by(Meal.position, Meal.meal_id)
    ).all()

    every_item = [item for meal in meals for item in meal.items]
    totals = _totals(every_item)

    target = db.scalar(
        select(NutritionTarget).where(NutritionTarget.user_id == current_user.id)
    )

    return DayRead(
        logged_date=logged_date,
        meals=[_to_read(meal) for meal in meals],
        totals=totals,
        planned_kcal=_planned_kcal(every_item),
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
    """Rename a meal, or set its note or photo."""
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
    """Remove a whole meal and everything logged or planned in it."""
    meal = _owned_meal(db, meal_id, current_user.id)
    db.delete(meal)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------
# Foods in a meal
# --------------------------------------------------------------------------


@router.post("/meal-items", response_model=MealItemRead, status_code=status.HTTP_201_CREATED)
def log_meal_item(
    payload: MealItemCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Log or plan one food into a meal.

    The nutrition is computed here and **written into the row**, not looked up
    on read. Correcting the catalog next month must not rewrite whether the
    user hit their target today.

    A planned item also stores its amount as the plan, so that ticking it
    later with a different amount leaves what was planned on record.
    """
    meal = _owned_meal(db, payload.meal_id, current_user.id)

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

    return _append_item(
        db,
        meal,
        MealItem(
            meal_id=meal.meal_id,
            food_id=food.food_id,
            food_name=_food_name(food)[:200],
            quantity=payload.quantity,
            unit=payload.unit,
            status=payload.status,
            **values,
        ),
    )


def _times(value: Decimal | None, quantity: Decimal) -> Decimal | None:
    return _cents(value * quantity) if value is not None else None


@router.post(
    "/meal-items/custom",
    response_model=MealItemRead,
    status_code=status.HTTP_201_CREATED,
)
def log_custom_item(
    payload: MealItemCustomCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Log or plan a food the catalog does not have, typed in by hand.

    Written into the row like any other entry - nutrition lives on meal_items,
    so nothing is added to the shared catalog. The figures arrive per serving
    and are multiplied out here, so the row holds what was actually eaten,
    the same as a catalog entry does.
    """
    meal = _owned_meal(db, payload.meal_id, current_user.id)
    q = payload.quantity

    return _append_item(
        db,
        meal,
        MealItem(
            meal_id=meal.meal_id,
            food_id=None,
            is_custom=True,
            food_name=payload.name,
            quantity=q,
            unit=UNIT_SERVING,
            grams=None,
            status=payload.status,
            energy_kcal=_cents(payload.energy_kcal * q),
            protein_g=_times(payload.protein_g, q),
            fat_g=_times(payload.fat_g, q),
            carb_g=_times(payload.carb_g, q),
        ),
    )


# How far back "recent" looks. Weeks of ordinary eating; a food not seen in
# the last few hundred entries is not recent.
RECENT_SCAN = 300


def _per_serving(value: Decimal | None, quantity: Decimal) -> Decimal | None:
    return _cents(value / quantity) if value is not None else None


@router.get("/meal-items/recent", response_model=list[RecentFood])
def recent_foods(
    current_user: CurrentUserDep,
    db: DbSession,
    limit: int = Query(15, ge=1, le=50),
    language: str = Query(DEFAULT_LANG, min_length=2, max_length=5),
) -> list[RecentFood]:
    """What the user has logged lately, newest first, each food once.

    This is what the food picker shows before anything is typed: most meals
    repeat, and somebody logging their usual breakfast should not have to
    search for it. Catalog foods come back with today's figures; hand-typed
    ones with the figures last typed, per serving, so tapping one fills the
    form in again.
    """
    items = db.scalars(
        select(MealItem)
        .join(Meal, Meal.meal_id == MealItem.meal_id)
        .where(Meal.user_id == current_user.id)
        .order_by(MealItem.created_at.desc(), MealItem.meal_item_id.desc())
        .limit(RECENT_SCAN)
    ).all()

    picked: list[MealItem] = []
    seen: set[tuple] = set()
    for item in items:
        if item.is_custom:
            key = ("custom", item.food_name.strip().lower())
        elif item.food_id is not None:
            key = ("food", item.food_id)
        else:
            continue  # a catalog food since removed: nothing to log again
        if key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) >= limit:
            break

    food_ids = [i.food_id for i in picked if not i.is_custom]
    summaries = {}
    if food_ids:
        names = _name_for(language)
        rows = db.execute(
            select(Food, names.c.name, FoodCategory.name_vi)
            .join(names, names.c.food_id == Food.food_id)
            .outerjoin(
                FoodCategory, FoodCategory.food_category_id == Food.food_category_id
            )
            .where(Food.food_id.in_(food_ids))
        ).all()
        components = _component_names(db, food_ids)
        summaries = {
            row[0].food_id: _to_summary(row, components.get(row[0].food_id))
            for row in rows
        }

    result: list[RecentFood] = []
    for item in picked:
        if item.is_custom:
            q = item.quantity
            result.append(RecentFood(
                kind="custom",
                custom=CustomFood(
                    name=item.food_name,
                    energy_kcal=_per_serving(item.energy_kcal, q),
                    protein_g=_per_serving(item.protein_g, q),
                    fat_g=_per_serving(item.fat_g, q),
                    carb_g=_per_serving(item.carb_g, q),
                ),
                last_quantity=item.quantity,
                last_unit=item.unit,
                last_used_at=item.created_at,
            ))
        elif item.food_id in summaries:
            result.append(RecentFood(
                kind="catalog",
                food=summaries[item.food_id],
                last_quantity=item.quantity,
                last_unit=item.unit,
                last_used_at=item.created_at,
            ))
    return result


@router.put("/meal-items/{item_id}", response_model=MealItemRead)
def update_meal_item(
    item_id: int,
    payload: MealItemUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Correct an amount.

    The nutrition is recomputed from **today's** catalog figures, because the
    user is restating an amount rather than asking to keep an old number.

    Editing something still planned revises the plan, so the plan follows it.
    Editing something already eaten corrects what was eaten and leaves the plan
    alone - that difference is the whole point of keeping it.
    """
    item = _owned_item(db, item_id, current_user.id)

    _restate(db, item, payload.quantity, payload.unit)
    if item.status == STATUS_PLANNED:
        item.planned_quantity = item.quantity
        item.planned_unit = item.unit
        item.planned_energy_kcal = item.energy_kcal
    item.updated_at = func.now()

    db.commit()
    db.refresh(item)
    return MealItemRead.model_validate(item)


@router.put("/meal-items/{item_id}/status", response_model=MealItemRead)
def set_meal_item_status(
    item_id: int,
    payload: MealItemStatusUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> MealItemRead:
    """Tick a planned item as eaten, or put it back on the plan.

    Ticking with an amount records what was actually eaten; without one it
    means "ate it as planned". Putting an item back restores the planned
    amount - an accidental tap should be undoable without retyping anything.
    Both directions are idempotent, so a double tap changes nothing.
    """
    item = _owned_item(db, item_id, current_user.id)

    if (payload.quantity is None) != (payload.unit is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Send quantity and unit together, or neither.",
        )

    if payload.status == STATUS_EATEN:
        if payload.quantity is not None:
            _restate(db, item, payload.quantity, payload.unit)
        item.status = STATUS_EATEN

    else:
        if payload.quantity is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A planned item takes the planned amount; send no quantity.",
            )
        if item.planned_quantity is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This entry was never planned, so there is no plan to put "
                    "it back to. Delete it instead."
                ),
            )
        if item.status != STATUS_PLANNED:
            _restate(db, item, item.planned_quantity, item.planned_unit)
            item.planned_energy_kcal = item.energy_kcal
        item.status = STATUS_PLANNED

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
    """Remove one food. The meal stays: the user created it, and an empty one
    is where the next food goes."""
    item = _owned_item(db, item_id, current_user.id)
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
