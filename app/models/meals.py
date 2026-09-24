from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.foods import Food

# Fixed rather than free text: the diary screen is four sections, and a fifth
# spelling of "lunch" would render as a section nobody asked for.
MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

# What `quantity` on a meal item is counted in.
UNIT_SERVING = "serving"
UNIT_GRAM = "gram"


class Meal(Base):
    """One sitting: a user, a day, and which of the four meals it was.

    Unique on (user_id, logged_date, meal_type), which is what keeps the diary
    well-formed - there is exactly one lunch on any given day, and adding a
    second food to it appends an item rather than creating a rival lunch. Two
    snacks in an afternoon both land under the one `snack` row.

    `note` and `photo_key` live here rather than on the items because they
    describe the sitting, not any one food in it.
    """

    __tablename__ = "meals"
    __table_args__ = (
        CheckConstraint(
            "meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')",
            name="chk_meals_type",
        ),
        UniqueConstraint(
            "user_id", "logged_date", "meal_type", name="uq_meals_user_date_type"
        ),
        Index("idx_meals_user_date", "user_id", "logged_date"),
    )

    meal_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    logged_date: Mapped[date] = mapped_column(Date, nullable=False)
    meal_type: Mapped[str] = mapped_column(String(16), nullable=False)

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

    @property
    def energy_kcal(self) -> Decimal:
        """Total for the sitting, summed on read.

        Not a stored column, for the same reason BMI is not one: a cached
        total is a second source of truth that drifts from the first the
        moment an item is edited.
        """
        return sum((item.energy_kcal for item in self.items), Decimal("0"))


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
    """

    __tablename__ = "meal_items"
    __table_args__ = (
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
