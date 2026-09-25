from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.foods import Food

# Whether a food has been eaten yet. A planned item is ticked when it is eaten.
STATUS_PLANNED = "planned"
STATUS_EATEN = "eaten"

# What `quantity` on a meal item is counted in.
UNIT_SERVING = "serving"
UNIT_GRAM = "gram"


class Meal(Base):
    """One sitting on one day, named by the user.

    `name` is NULL until the user types one; the app shows "Bữa 1", "Bữa 2"
    by position as a placeholder, so nothing is stored that the user did not
    write. There is no UNIQUE on (user, date, position): positions are only
    appended (max + 1) and reads break ties by `meal_id`, so two concurrent
    creates cost a harmless tie rather than a unique-key error.

    The table still has a legacy `meal_type` column - kept, nullable and
    unmapped, so the previous backend build keeps working while this one
    deploys. See sql_script/alter_meals_planning.sql.

    `note` and `photo_key` live here rather than on the items because they
    describe the sitting, not any one food in it.
    """

    __tablename__ = "meals"
    __table_args__ = (
        CheckConstraint(
            "name IS NULL OR length(btrim(name)) > 0", name="chk_meals_name"
        ),
        Index("idx_meals_user_date", "user_id", "logged_date"),
        Index(
            "idx_meals_user_date_position",
            "user_id",
            "logged_date",
            "position",
            "meal_id",
        ),
    )

    meal_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    logged_date: Mapped[date] = mapped_column(Date, nullable=False)

    name: Mapped[str | None] = mapped_column(String(60))
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    note: Mapped[str | None] = mapped_column(Text)
    # A storage key, never a URL - a signed URL written into the database sits
    # there expiring. Same rule as user_profiles.avatar_url.
    photo_key: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    items: Mapped[list["MealItem"]] = relationship(
        back_populates="meal",
        cascade="all, delete-orphan",
        order_by="MealItem.sort_order",
    )


class MealItem(Base):
    """One food eaten, with its nutrition frozen at the moment it was logged.

    **The macros here are a copy, not a lookup.** Correcting a catalog row next
    month must not rewrite whether somebody hit their target last Tuesday -
    the same rule `session_sets.target_*` already follows. `food_id` keeps the
    link for anyone who wants today's figure and is ON DELETE SET NULL, so
    tidying the catalog unlinks a diary entry rather than deleting it; that is
    what `food_name` is for.

    `quantity` + `unit` is the pair the user typed. `grams` is what it works
    out to and is nullable on purpose: 237 of the Institute's dishes publish no
    serving weight, so "1 suất phở" is a complete fact whose weight is
    genuinely unknown, and guessing would be worse than saying so.

    **`status` says whether it has been eaten.** A 'planned' item is ticked
    when it is eaten, optionally with a different amount. The plan is kept in
    `planned_*` next to the actual so "planned 1 bowl, ate half" stays
    answerable - the same rule `session_sets.target_*` follows. An item added
    directly as eaten has no plan, and the three planned columns are then all
    NULL.
    """

    __tablename__ = "meal_items"
    __table_args__ = (
        CheckConstraint("status IN ('planned', 'eaten')", name="chk_meal_items_status"),
        CheckConstraint(
            "num_nonnulls(planned_quantity, planned_unit, planned_energy_kcal) IN (0, 3)",
            name="chk_meal_items_plan_shape",
        ),
        CheckConstraint(
            "status <> 'planned' OR planned_quantity IS NOT NULL",
            name="chk_meal_items_planned_has_plan",
        ),
        CheckConstraint(
            "NOT is_custom OR (food_id IS NULL AND unit = 'serving')",
            name="chk_meal_items_custom_shape",
        ),
        CheckConstraint("quantity > 0", name="chk_meal_items_quantity"),
        CheckConstraint("unit IN ('serving', 'gram')", name="chk_meal_items_unit"),
        CheckConstraint("grams IS NULL OR grams > 0", name="chk_meal_items_grams"),
        CheckConstraint("energy_kcal >= 0", name="chk_meal_items_energy"),
        Index("idx_meal_items_meal", "meal_id", "sort_order"),
    )

    meal_item_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    meal_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("meals.meal_id", ondelete="CASCADE"), nullable=False
    )
    food_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("foods.food_id", ondelete="SET NULL")
    )

    food_name: Mapped[str] = mapped_column(String(200), nullable=False)

    quantity: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(10), nullable=False)
    grams: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))

    energy_kcal: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    carb_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=STATUS_EATEN
    )
    # Typed in by hand rather than picked from the catalog. `food_id` is NULL
    # then, as it is for a catalog row removed later; this flag is what says
    # the entry can still be rescaled from its own per-serving figures.
    is_custom: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    planned_quantity: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    planned_unit: Mapped[str | None] = mapped_column(String(10))
    planned_energy_kcal: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    meal: Mapped["Meal"] = relationship(back_populates="items")
    food: Mapped["Food | None"] = relationship(viewonly=True)
