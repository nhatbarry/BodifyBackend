from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    pass

# The two kinds of row this table holds. They are not interchangeable and the
# diary has to ask for a different number depending on which one it is looking
# at - grams for an ingredient, servings for a dish.
BASIS_PER_100G = "per_100g"
BASIS_PER_SERVING = "per_serving"


class FoodCategory(Base):
    """The source's own grouping, kept rather than remapped onto one of ours."""

    __tablename__ = "food_categories"
    __table_args__ = (
        UniqueConstraint("source", "source_code", name="uq_food_categories_source"),
    )

    food_category_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)

    name_vi: Mapped[str] = mapped_column(String(200), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(200))

    foods: Mapped[list["Food"]] = relationship(back_populates="category", viewonly=True)


class Food(Base):
    """One thing a user can log, whether an ingredient or a finished dish.

    `basis` is the column everything else here bends around. A `per_100g` row
    publishes its nutrition per 100 g, the way every food composition table in
    the world does. A `per_serving` row publishes it per serving, the way the
    National Institute of Nutrition does for cooked dishes - and the weight of
    that serving is not part of what they publish, which is why
    `serving_grams` is nullable and carries a `serving_source` saying where the
    number came from:

        nin_label   read off the dish's own information card
        derived     inferred from a name match against a known food
        NULL        genuinely unknown - log it in servings, not grams
    """

    __tablename__ = "foods"
    __table_args__ = (
        CheckConstraint(
            "basis IN ('per_100g', 'per_serving')", name="chk_foods_basis"
        ),
        CheckConstraint("energy_kcal >= 0", name="chk_foods_energy"),
        CheckConstraint(
            "serving_grams IS NULL OR serving_grams > 0", name="chk_foods_serving"
        ),
        UniqueConstraint("source", "source_code", name="uq_foods_source"),
        Index("idx_foods_category", "food_category_id"),
    )

    food_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    source: Mapped[str] = mapped_column(String(16), nullable=False)
    source_code: Mapped[str | None] = mapped_column(String(64))

    food_category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("food_categories.food_category_id", ondelete="SET NULL")
    )

    basis: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=BASIS_PER_100G
    )
    serving_grams: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    serving_source: Mapped[str | None] = mapped_column(String(16))

    # NOT NULL because every source publishes energy. The macros are nullable
    # because 30 of the Institute's 1247 dishes are missing a fat or carb
    # figure, and a zero would read as "this dish has no fat".
    energy_kcal: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    protein_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    fat_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    carb_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    fiber_g: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # Everything else the source published, kept rather than modelled: vitamins
    # and minerals differ per source and nothing reads them yet.
    nutrients: Mapped[dict | None] = mapped_column(JSONB)

    image_url: Mapped[str | None] = mapped_column(Text)
    image_key: Mapped[str | None] = mapped_column(Text)

    # A duplicate of a more complete row with the same name. Left out of every
    # catalog listing but still reachable by id, so diary entries logged
    # against it keep working. Set by sql_script/hide_duplicate_foods.sql.
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    category: Mapped["FoodCategory | None"] = relationship(
        back_populates="foods", viewonly=True
    )
    translations: Mapped[list["FoodTranslation"]] = relationship(
        back_populates="food", cascade="all, delete-orphan"
    )
    components: Mapped[list["FoodComponent"]] = relationship(
        back_populates="food",
        cascade="all, delete-orphan",
        order_by="FoodComponent.sort_order",
    )

    @property
    def is_per_serving(self) -> bool:
        return self.basis == BASIS_PER_SERVING


class FoodTranslation(Base):
    """The name, per language.

    `name_ascii` is the unaccented form, written once at seed time so search
    can match "banh mi" against "bánh mì" without running unaccent() over the
    whole table on every keystroke. Applied to free-text search only - never to
    an exact filter, because unaccent("Tạ đòn") == unaccent("Tạ đơn") and that
    collision already cost this project a bug once.
    """

    __tablename__ = "food_translations"
    __table_args__ = (
        UniqueConstraint("food_id", "language_code", name="uq_food_translations"),
        Index("idx_food_translations_ascii", "language_code", "name_ascii"),
    )

    food_translation_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    food_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foods.food_id", ondelete="CASCADE"), nullable=False
    )
    language_code: Mapped[str] = mapped_column(String(5), nullable=False)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_ascii: Mapped[str | None] = mapped_column(String(200))

    food: Mapped["Food"] = relationship(back_populates="translations")


class FoodComponent(Base):
    """What a dish was made of, as printed on its information card.

    Display detail, not arithmetic: the dish's own macros come from the source
    and are not recomputed from these. A component whose grams are unknown
    still belongs in the list.
    """

    __tablename__ = "food_components"
    __table_args__ = (
        CheckConstraint("grams IS NULL OR grams > 0", name="chk_food_components_grams"),
        Index("idx_food_components_food", "food_id", "sort_order"),
    )

    food_component_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    food_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("foods.food_id", ondelete="CASCADE"), nullable=False
    )

    name_vi: Mapped[str] = mapped_column(String(200), nullable=False)
    grams: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))

    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="nin_label"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    food: Mapped["Food"] = relationship(back_populates="components")
