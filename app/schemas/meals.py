from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MealType = Literal["breakfast", "lunch", "dinner", "snack"]
Unit = Literal["serving", "gram"]

# Wide enough for anything real, narrow enough to catch a slip. 50 servings of
# one dish in one sitting is a typo, and so is 20 kg of rice; both would poison
# a daily total the user then has to hunt through to find.
MAX_QUANTITY = Decimal("10000")


class MealItemCreate(BaseModel):
    """Log one food into a meal.

    The meal itself is addressed by (date, type) rather than by id: the client
    knows the user tapped "add to lunch", not which row that is, and there is
    exactly one lunch per day for it to land in.
    """

    logged_date: date
    meal_type: MealType
    food_id: int

    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY, max_digits=8, decimal_places=2)
    # Which unit is accepted depends on the food's `basis`; the server checks
    # and answers 422 with the reason rather than silently converting.
    unit: Unit


class MealItemUpdate(BaseModel):
    """Correct an amount already logged.

    Only the amount. The food is not swappable - that is a delete and a new
    entry, and allowing it would mean a row whose snapshot came from one food
    while pointing at another.
    """

    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY, max_digits=8, decimal_places=2)
    unit: Unit


class MealItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meal_item_id: int
    # NULL once a catalog row has been removed. `food_name` still says what
    # was eaten, which is the point of snapshotting it.
    food_id: int | None
    food_name: str

    quantity: Decimal
    unit: str
    grams: Decimal | None

    energy_kcal: Decimal
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    fiber_g: Decimal | None

    created_at: datetime


class MealUpdate(BaseModel):
    """Notes and a photo belong to the sitting, not to any food in it."""

    note: str | None = Field(default=None, max_length=2000)
    photo_key: str | None = Field(default=None, max_length=500)


class Totals(BaseModel):
    """Summed on read, never stored - a cached total drifts from its items."""

    energy_kcal: Decimal = Decimal("0")
    protein_g: Decimal = Decimal("0")
    fat_g: Decimal = Decimal("0")
    carb_g: Decimal = Decimal("0")
    fiber_g: Decimal = Decimal("0")


class MealRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    meal_id: int
    logged_date: date
    meal_type: str
    note: str | None
    photo_key: str | None

    items: list[MealItemRead] = []
    totals: Totals = Totals()


class NutritionTargetUpsert(BaseModel):
    """Only energy is required - counting calories alone is the common case."""

    energy_kcal: Decimal = Field(
        gt=0, le=Decimal("20000"), max_digits=7, decimal_places=1
    )
    protein_g: Decimal | None = Field(
        default=None, ge=0, le=Decimal("2000"), max_digits=7, decimal_places=1
    )
    fat_g: Decimal | None = Field(
        default=None, ge=0, le=Decimal("2000"), max_digits=7, decimal_places=1
    )
    carb_g: Decimal | None = Field(
        default=None, ge=0, le=Decimal("2000"), max_digits=7, decimal_places=1
    )


class NutritionTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    energy_kcal: Decimal
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    updated_at: datetime


class DayRead(BaseModel):
    """One day of the diary: the four sittings, the totals, and the target.

    `remaining_kcal` is None when no target is set rather than 0, because
    "you have 0 left" and "you never said what you were aiming for" are
    different answers and the screen shows them differently.
    """

    logged_date: date
    meals: list[MealRead]
    totals: Totals
    target: NutritionTargetRead | None = None
    remaining_kcal: Decimal | None = None


class DaySummary(BaseModel):
    """One row of the history strip: a date and what it added up to."""

    logged_date: date
    energy_kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carb_g: Decimal
    fiber_g: Decimal
    meal_count: int
