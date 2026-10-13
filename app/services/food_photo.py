"""From what the model said about a plate to a draft the diary can accept.

The division of labour is the whole design:

* **The model names the dishes and judges the portion.** That is what a
  picture can tell you.
* **The catalog supplies the nutrition** wherever it has the dish. The
  Institute's figures were measured; the model's are a guess from pixels, and
  a guess must not quietly displace a measurement in somebody's daily total.
* **The model's own figures survive only as a fallback**, for a dish the
  catalog does not have, and the app labels them as an AI estimate.

Nothing here writes to the diary. The draft goes back to the user, who
confirms it through the same /meal-items endpoints as everything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api.endpoints.foods import (
    DEFAULT_LANG,
    _component_names,
    _name_for,
    _to_summary,
    search_clauses,
    summary_query,
)
from app.models.foods import BASIS_PER_SERVING, Food
from app.models.meals import ANALYSIS_OK, UNIT_GRAM, UNIT_SERVING, MealPhotoAnalysis
from app.schemas.meals import DetectedFood, PhotoCandidate, PhotoEstimate
from app.services.nutrition import portion

# More than this on one plate is the model listing garnish.
MAX_ITEMS = 8
# Shown as a choice under each detected dish. Three is what fits on a phone
# without scrolling and covers "Phở bò" / "Phở gà" / "Phở" for a bowl of phở.
CANDIDATES = 3
MAX_SEARCH_TERMS = 4

# The same bounds the diary's own forms enforce, so a draft is always
# saveable: a serving count in quarters, grams in fives.
MIN_SERVINGS, MAX_SERVINGS = Decimal("0.25"), Decimal("20")
MIN_GRAMS, MAX_GRAMS = Decimal("5"), Decimal("3000")
MAX_KCAL_PER_SERVING = Decimal("10000")
MAX_MACRO_PER_SERVING = Decimal("1000")


@dataclass
class Detected:
    """One dish as the model reported it, after clamping. Totals are for the
    whole visible portion, not per serving."""

    name: str
    terms: list[str]
    servings: Decimal
    grams: Decimal | None
    energy_kcal: Decimal
    protein_g: Decimal | None
    fat_g: Decimal | None
    carb_g: Decimal | None
    confidence: float


def _decimal(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def _step(value: Decimal, step: Decimal, low: Decimal, high: Decimal) -> Decimal:
    """Round to the nearest `step` and keep within bounds."""
    rounded = (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step
    return min(max(rounded, low), high)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def clean(raw: dict) -> tuple[bool, list[Detected]]:
    """Check and clamp the model's answer.

    Structured output guarantees the JSON parses, not that the numbers are
    sane: a model can still say 0 servings or 40 kg of rice. Anything without
    a name or an energy figure is dropped rather than repaired.
    """
    is_food = bool(raw.get("is_food"))
    items = raw.get("items")
    if not isinstance(items, list):
        return is_food, []

    detected: list[Detected] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name_vi") or "").strip()[:200]
        energy = _decimal(entry.get("energy_kcal"))
        if not name or energy is None:
            continue

        # Missing means "one"; a stated 0 is clamped up, since the model did
        # say the dish is on the plate.
        servings = _decimal(entry.get("servings"))
        servings = _step(
            Decimal("1") if servings is None else servings,
            Decimal("0.25"), MIN_SERVINGS, MAX_SERVINGS,
        )
        grams = _decimal(entry.get("grams"))
        grams = _step(grams, Decimal("5"), MIN_GRAMS, MAX_GRAMS) if grams else None

        terms: list[str] = []
        seen: set[str] = set()
        raw_terms = entry.get("search_terms")
        for term in [name, *(raw_terms if isinstance(raw_terms, list) else [])]:
            term = str(term or "").strip()[:60]
            if term and term.lower() not in seen:
                seen.add(term.lower())
                terms.append(term)

        try:
            confidence = float(entry.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0

        detected.append(
            Detected(
                name=name,
                terms=terms[:MAX_SEARCH_TERMS],
                servings=servings,
                grams=grams,
                energy_kcal=energy,
                protein_g=_decimal(entry.get("protein_g")),
                fat_g=_decimal(entry.get("fat_g")),
                carb_g=_decimal(entry.get("carb_g")),
                confidence=min(max(confidence, 0.0), 1.0),
            )
        )
        if len(detected) == MAX_ITEMS:
            break

    return is_food, detected


def _find(db: Session, names, terms: list[str]) -> list[tuple]:
    """Up to CANDIDATES visible catalog rows, the full name's hits first.

    Stops as soon as there are enough: the shorter terms only widen the net
    when the model's name is more specific than anything the catalog has
    ("Phở bò tái lăn" -> "Phở bò").

    **Names must start with the term**, unlike the search box. Measured on a
    photo of cơm tấm: "mỡ hành" matched "Sò huyết nướng mỡ hành" as a
    substring, and the fallback "Cơm" put "Cơm gà" (1140 kcal) at the top for
    a plate of plain rice. A person typing can see that and scroll past; a
    preselected draft cannot. For the same reason a one-word fallback is not
    searched at all - only the model's own name may be that short ("Phở").
    """
    rows: list[tuple] = []
    seen: set[int] = set()
    for index, term in enumerate(terms):
        if index > 0 and len(term.split()) < 2:
            continue
        _, ranking = search_clauses(names, term)
        prefix = func.unaccent(f"{term}%")
        starts_with = or_(
            names.c.name_ascii.ilike(prefix),
            func.unaccent(names.c.name).ilike(prefix),
        )
        found = db.execute(
            summary_query(names)
            .where(starts_with, Food.is_hidden.is_(False))
            .order_by(*ranking)
            .limit(CANDIDATES)
        ).all()
        for row in found:
            if row[0].food_id not in seen:
                seen.add(row[0].food_id)
                rows.append(row)
        if len(rows) >= CANDIDATES:
            break
    return rows[:CANDIDATES]


def _amount_for(food: Food, item: Detected) -> tuple[Decimal, str]:
    """The amount to suggest, in the one unit this food is certain to accept.

    A dish is counted in servings, which is what the model judged directly.
    An ingredient can only be logged in grams; without a weight from the
    model, 100 g is offered - visibly a default, and the user is about to
    look at it anyway.
    """
    if food.basis == BASIS_PER_SERVING:
        return item.servings, UNIT_SERVING
    return item.grams or Decimal("100"), UNIT_GRAM


# How far the catalog's figure may sit from the model's own estimate before
# the match is presumed to be the wrong dish. Wide on purpose - the estimate is
# itself a guess, and portions vary - so only a different food trips it: a
# rice dish of 1140 kcal offered for a bowl the model put at 260.
PLAUSIBLE_RATIO = Decimal("3")


def _plausible(candidate_kcal: Decimal, estimated_kcal: Decimal) -> bool:
    low, high = sorted((candidate_kcal, estimated_kcal))
    if low <= 0:
        return high < 50  # both near nothing: tea, herbs, a slice of cucumber
    return high / low <= PLAUSIBLE_RATIO


def _per_serving(value: Decimal | None, servings: Decimal, cap: Decimal) -> Decimal | None:
    if value is None:
        return None
    return min(_cents(value / servings), cap)


def _estimate(item: Detected) -> PhotoEstimate:
    return PhotoEstimate(
        name=item.name,
        quantity=item.servings,
        energy_kcal=_per_serving(item.energy_kcal, item.servings, MAX_KCAL_PER_SERVING),
        protein_g=_per_serving(item.protein_g, item.servings, MAX_MACRO_PER_SERVING),
        fat_g=_per_serving(item.fat_g, item.servings, MAX_MACRO_PER_SERVING),
        carb_g=_per_serving(item.carb_g, item.servings, MAX_MACRO_PER_SERVING),
    )


def draft(
    db: Session, detected: list[Detected], language: str = DEFAULT_LANG
) -> list[DetectedFood]:
    """Match each detected dish to the catalog and price it both ways."""
    names = _name_for(language)
    matches = [_find(db, names, item.terms) for item in detected]
    components = _component_names(
        db, [row[0].food_id for rows in matches for row in rows]
    )

    result: list[DetectedFood] = []
    for item, rows in zip(detected, matches):
        candidates = []
        for row in rows:
            food = row[0]
            quantity, unit = _amount_for(food, item)
            values = portion(food, quantity, unit)
            candidates.append(
                PhotoCandidate(
                    food=_to_summary(row, components.get(food.food_id)),
                    quantity=quantity,
                    unit=unit,
                    grams=values["grams"],
                    energy_kcal=values["energy_kcal"],
                    protein_g=values["protein_g"],
                    fat_g=values["fat_g"],
                    carb_g=values["carb_g"],
                    plausible=_plausible(values["energy_kcal"], item.energy_kcal),
                )
            )
        # Plausible first, name order kept within each group (sort is stable),
        # so the preselected match is never one the numbers argue against.
        candidates.sort(key=lambda c: not c.plausible)
        result.append(
            DetectedFood(
                name=item.name,
                confidence=item.confidence,
                estimated_grams=item.grams,
                candidates=candidates,
                estimate=_estimate(item),
            )
        )
    return result


def used_today(db: Session, user_id) -> int:
    """Successful analyses in the last 24 hours. Rolling rather than by
    calendar day, so the limit means the same thing in every timezone."""
    return db.scalar(
        select(func.count())
        .select_from(MealPhotoAnalysis)
        .where(
            MealPhotoAnalysis.user_id == user_id,
            MealPhotoAnalysis.status == ANALYSIS_OK,
            MealPhotoAnalysis.created_at > func.now() - text("interval '24 hours'"),
        )
    ) or 0
