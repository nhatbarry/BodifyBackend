from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class FoodComponentRead(BaseModel):
    """One ingredient of a dish, as printed on its information card."""

    model_config = ConfigDict(from_attributes=True)

    name_vi: str
    grams: Decimal | None


class FoodSummary(BaseModel):
    """A search hit: enough to render a row and decide how to log it.

    `basis` is what the client reads to pick its input. `per_serving` asks for
    a number of servings; `per_100g` asks for grams. Getting this backwards is
    how a food app reports double somebody's intake, so it travels with every
    row rather than being inferred from the category.
    """

    model_config = ConfigDict(from_attributes=True)

    food_id: int
    name: str
    basis: str

    # Per 100 g or per serving, according to `basis`.
    energy_kcal: Decimal
    protein_g: Decimal | None = None
    fat_g: Decimal | None = None
    carb_g: Decimal | None = None
    fiber_g: Decimal | None = None

    # Only meaningful for per_serving rows, and NULL for 237 of them whose
    # label never published a weight. A client that wants to offer grams as an
    # input has to check this first.
    serving_grams: Decimal | None = None
    serving_source: str | None = None

    category_name: str | None = None

    # The first few ingredients, so three rows all reading "Phở gà" can be
    # told apart - one is mostly broth, another has two kinds of egg in it.
    # The Institute publishes no description for any dish (the field exists
    # and is empty in all 1247), so this breakdown, read off their own
    # label images, is the only thing that distinguishes them.
    component_names: list[str] = []

    # Signed from `foods.image_key` on read, never stored: a signed URL
    # written into the database sits there expiring. Stable for 24h so the
    # app's disk cache can actually hit - same rule as the exercise catalog.
    image_url: str | None = None


class FoodDetail(FoodSummary):
    """One food, with what it was made of if that is known."""

    source: str
    components: list[FoodComponentRead] = []
