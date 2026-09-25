from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.foods import FoodSummary

Unit = Literal["serving", "gram"]
ItemStatus = Literal["planned", "eaten"]

# Wide enough for anything real, narrow enough to catch a slip. 50 servings of
# one dish in one sitting is a typo, and so is 20 kg of rice; both would poison
# a daily total the user then has to hunt through to find.
MAX_QUANTITY = Decimal("10000")


class MealCreate(BaseModel):
    """Add a meal to a day. Empty is fine - the user is about to fill it, or is
    planning tomorrow and will fill it in the morning."""

    logged_date: date
    name: str | None = Field(default=None, max_length=60)

    @field_validator("name")
    @classmethod
    def _blank_is_no_name(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


def _blank_to_none(value: str | None) -> str | None:
    """A name of spaces is no name: the placeholder should show instead."""
    if value is None:
        return None
    value = value.strip()
    return value or None


class MealItemCreate(BaseModel):
    """Log or plan one food into a meal the user already created.

    `status` defaults to 'eaten'. A 'planned' item records what was intended:
    its amount is also kept as the plan, so it can later be compared with what
    was actually eaten.
    """

    meal_id: int
    food_id: int
    status: ItemStatus = "eaten"

    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY, max_digits=8, decimal_places=2)
    # Which unit is accepted depends on the food's `basis`; the server checks
    # and answers 422 with the reason rather than silently converting.
    unit: Unit


class MealItemCustomCreate(BaseModel):
    """A food the catalog does not have, typed in by hand.

    The figures are for **one serving**, the way a label or a restaurant menu
    states them; `quantity` is how many servings. Only energy is required -
    the same reasoning as the daily target: demanding macros somebody does
    not know is how an entry gets abandoned.
    """

    meal_id: int
    status: ItemStatus = "eaten"

    name: str = Field(min_length=1, max_length=200)
    energy_kcal: Decimal = Field(ge=0, le=Decimal("10000"), max_digits=8, decimal_places=2)
    protein_g: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"), max_digits=8, decimal_places=2)
    fat_g: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"), max_digits=8, decimal_places=2)
    carb_g: Decimal | None = Field(default=None, ge=0, le=Decimal("1000"), max_digits=8, decimal_places=2)

    quantity: Decimal = Field(
        default=Decimal("1"), gt=0, le=Decimal("100"), max_digits=8, decimal_places=2
    )

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("A name is required")
        return value


class MealItemUpdate(BaseModel):
    """Correct an amount already logged.

    Only the amount. The food is not swappable - that is a delete and a new
    entry, and allowing it would mean a row whose snapshot came from one food
    while pointing at another.
    """

    quantity: Decimal = Field(gt=0, le=MAX_QUANTITY, max_digits=8, decimal_places=2)
    unit: Unit


class MealItemStatusUpdate(BaseModel):
    """Tick a planned item as eaten, or put it back.

    Ticking may carry the amount that was actually eaten (both fields or
    neither); leaving them out means "ate it as planned". Putting an item back
    to 'planned' restores the amount that was planned.
    """

    status: ItemStatus
    quantity: Decimal | None = Field(
        default=None, gt=0, le=MAX_QUANTITY, max_digits=8, decimal_places=2
    )
    unit: Unit | None = None


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

    status: str
    is_custom: bool
    # What was planned, kept after the item is eaten so the two can be
    # compared. All None for something that was never planned.
    planned_quantity: Decimal | None
    planned_unit: str | None
    planned_energy_kcal: Decimal | None

    created_at: datetime


class MealUpdate(BaseModel):
    """The name, note and photo belong to the sitting, not to any food in it.

    Omitting a field keeps it; sending null clears it (a cleared name goes back
    to the "Bữa N" placeholder).
    """

    name: str | None = Field(default=None, max_length=60)
    note: str | None = Field(default=None, max_length=2000)
    photo_key: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _blank_is_no_name(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


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
    name: str | None
    position: int
    note: str | None
    photo_key: str | None

    items: list[MealItemRead] = []
    # What was actually eaten. Planned items are not in it.
    totals: Totals = Totals()
    # What the plan said this sitting would come to, whether or not it has been
    # eaten yet. Energy only - the plan snapshot does not carry macros.
    planned_kcal: Decimal = Decimal("0")


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
    """One day of the diary: its meals in order, the totals, and the target.

    `totals` and `remaining_kcal` count what was eaten. `planned_kcal` is what
    the plan said the day would come to, so a future day - nothing eaten yet -
    still has a number to show against the target.

    `remaining_kcal` is None when no target is set rather than 0, because
    "you have 0 left" and "you never said what you were aiming for" are
    different answers and the screen shows them differently.
    """

    logged_date: date
    meals: list[MealRead]
    totals: Totals
    planned_kcal: Decimal = Decimal("0")
    target: NutritionTargetRead | None = None
    remaining_kcal: Decimal | None = None


class CustomFood(BaseModel):
    """A hand-typed food as it was last entered, per serving."""

    name: str
    energy_kcal: Decimal
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carb_g: Decimal | None = None


class RecentFood(BaseModel):
    """Something the user has eaten before, for logging it again in one tap.

    Exactly one of `food` (a catalog row, current figures) and `custom` (a
    hand-typed entry, the figures last typed) is set. `last_quantity` and
    `last_unit` are how much they had last time - the likeliest amount now.
    """

    kind: Literal["catalog", "custom"]
    food: FoodSummary | None = None
    custom: CustomFood | None = None
    last_quantity: Decimal
    last_unit: str
    last_used_at: datetime


class CalendarDay(BaseModel):
    """One date on the diary's month calendar: whether anything was eaten or
    planned, and how much. Only dates with at least one food come back."""

    logged_date: date
    eaten_kcal: Decimal
    planned_kcal: Decimal
    eaten_count: int
    planned_count: int


class DaySummary(BaseModel):
    """One row of the history strip: a date and what it added up to."""

    logged_date: date
    energy_kcal: Decimal
    protein_g: Decimal
    fat_g: Decimal
    carb_g: Decimal
    fiber_g: Decimal
    meal_count: int
