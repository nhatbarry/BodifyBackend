"""Turning "1.5 of that" into calories.

The catalog holds two kinds of row that cannot be scaled the same way, and
this module is the one place that knows the difference:

    per_100g    an ingredient. 250 g of rice is 2.5 x the published figures.
    per_serving a cooked dish. 1.5 bowls of phở is 1.5 x them.

So the multiplier depends on `basis`, and which units are even accepted
depends on it too. Four combinations, and two of them are refusals:

    per_100g    + gram     ->  factor = grams / 100
    per_100g    + serving  ->  422. Nobody published what one serving of raw
                               pork is, and inventing 100 g would silently
                               misreport every such entry. This is the gap
                               `food_portions` was designed to fill and has
                               no data for yet.
    per_serving + serving  ->  factor = quantity
    per_serving + gram     ->  factor = grams / serving_grams, but only for
                               the 966 dishes whose serving weight was read
                               off their label. For the other 237 the weight
                               is genuinely unknown, so this is a 422 too.

Everything returned here is *copied into* `meal_items`, never read back
through a join. That is what keeps last Tuesday's total from moving when a
catalog row is corrected next month.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status

from app.models.foods import BASIS_PER_SERVING, Food
from app.models.meals import UNIT_GRAM, UNIT_SERVING

# The macro columns copied onto a meal item, in the order they are displayed.
MACRO_FIELDS = ("protein_g", "fat_g", "carb_g", "fiber_g")

_CENTS = Decimal("0.01")
_TENTHS = Decimal("0.1")


def _round(value: Decimal, exp: Decimal = _CENTS) -> Decimal:
    return value.quantize(exp, rounding=ROUND_HALF_UP)


def _reject(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail
    )


def scale_factor(food: Food, quantity: Decimal, unit: str) -> tuple[Decimal, Decimal | None]:
    """How many times the published figures, and what it weighs.

    Returns (factor, grams). `grams` is None only when the weight is genuinely
    unknown - a dish logged in servings whose label never published one. That
    is a complete diary entry; it just cannot also answer "how many grams".
    """
    if food.basis == BASIS_PER_SERVING:
        if unit == UNIT_SERVING:
            grams = (
                _round(quantity * food.serving_grams, _TENTHS)
                if food.serving_grams
                else None
            )
            return quantity, grams

        if unit == UNIT_GRAM:
            if not food.serving_grams:
                raise _reject(
                    f"'{_name_hint(food)}' is measured per serving and its serving "
                    "weight is unknown, so it cannot be logged in grams. "
                    "Log it in servings instead."
                )
            return _round(quantity / food.serving_grams, Decimal("0.0001")), _round(
                quantity, _TENTHS
            )

    else:  # per_100g
        if unit == UNIT_GRAM:
            return _round(quantity / Decimal("100"), Decimal("0.0001")), _round(
                quantity, _TENTHS
            )

        if unit == UNIT_SERVING:
            raise _reject(
                f"'{_name_hint(food)}' is measured per 100 g, and no serving size "
                "is published for it. Log it in grams instead."
            )

    raise _reject(f"Unknown unit '{unit}'")


def _name_hint(food: Food) -> str:
    """A name for an error message, without forcing the caller to join."""
    for translation in food.translations or []:
        if translation.language_code == "vi":
            return translation.name
    if food.translations:
        return food.translations[0].name
    return f"food {food.food_id}"


def portion(food: Food, quantity: Decimal, unit: str) -> dict:
    """The values to copy onto a meal item for `quantity` `unit` of `food`.

    A macro the catalog never published stays None rather than becoming zero:
    30 of the Institute's dishes are missing a fat or carbohydrate figure, and
    a zero there would read as "this dish has no fat" in a daily total.
    """
    factor, grams = scale_factor(food, quantity, unit)

    values: dict[str, Decimal | None] = {
        "grams": grams,
        "energy_kcal": _round(food.energy_kcal * factor),
    }
    for field in MACRO_FIELDS:
        published = getattr(food, field, None)
        values[field] = _round(published * factor) if published is not None else None
    return values
