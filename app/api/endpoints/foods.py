"""Finding something to log.

The diary is unusable without this: every entry starts with the user typing
part of a name. Read-only - the catalog is seeded from the Institute's data
and the USDA files, not written through the API.
"""

from __future__ import annotations

import unicodedata

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, func, or_, select

from app.api.dependencies import DbSession
from app.models.foods import Food, FoodCategory, FoodComponent, FoodTranslation
from app.schemas.common import Page
from app.schemas.foods import FoodComponentRead, FoodDetail, FoodSummary
from app.services.storage import presigned_url

router = APIRouter(prefix="/foods", tags=["foods"])

DEFAULT_LANG = "vi"


def _name_for(language: str):
    """One translation row per food: requested language, else any.

    Same shape as `best_translation` for exercises, and for the same reason -
    `foods` has no name column, so without this every row comes back nameless.
    """
    preference = case((FoodTranslation.language_code == language, 0), else_=1)
    ranked = select(
        FoodTranslation.food_id.label("food_id"),
        FoodTranslation.name.label("name"),
        FoodTranslation.name_ascii.label("name_ascii"),
        func.row_number()
        .over(partition_by=FoodTranslation.food_id, order_by=preference)
        .label("rank"),
    ).subquery()
    return select(ranked).where(ranked.c.rank == 1).subquery()


def _vietnamese_only(name: str) -> str:
    """Drop the English half of a bilingual ingredient name.

    214 of the Institute's label cards print both languages, so 1323 of the
    5663 ingredient rows read "Bánh phở/Pho (rice noodle)". On a Vietnamese
    screen the second half is noise that pushes the useful part off the line.

    Split only when the tail carries no Vietnamese diacritics, which is what
    makes this safe: "Bánh rân bọc đường (60g/cái)" keeps its slash because
    "cái" is Vietnamese, and cutting there would leave "(60g" dangling.
    """
    head, slash, tail = name.partition("/")
    if not slash:
        return name
    decomposed = unicodedata.normalize("NFD", tail)
    is_vietnamese = "đ" in tail.lower() or any(
        unicodedata.category(c) == "Mn" for c in decomposed
    )
    return name if is_vietnamese else head.strip()


def _to_summary(row, component_names: list[str] | None = None) -> FoodSummary:
    food, name, category_name = row
    return FoodSummary(
        food_id=food.food_id,
        name=name,
        basis=food.basis,
        energy_kcal=food.energy_kcal,
        protein_g=food.protein_g,
        fat_g=food.fat_g,
        carb_g=food.carb_g,
        fiber_g=food.fiber_g,
        serving_grams=food.serving_grams,
        serving_source=food.serving_source,
        category_name=category_name,
        # `foods.image_url` holds the path the picture came from on
        # viendinhduong.vn and is kept for provenance only - it is not
        # reachable from the app. The servable copy is ours, under
        # `image_key`, and is signed here.
        image_url=presigned_url(food.image_key) if food.image_key else None,
        component_names=[_vietnamese_only(n) for n in component_names or []],
    )


# How many ingredients a list row shows before it would wrap past usefulness.
# The full breakdown is on `GET /foods/{id}`.
SUMMARY_COMPONENTS = 6


def _component_names(db, food_ids: list[int]) -> dict[int, list[str]]:
    """`{food_id: [ingredient, ...]}` for a whole page in one query.

    The list needs this because the catalog surveys the same dish in several
    places and the names collide: three rows reading "Phở gà" are told apart
    only by what is in the bowl - one is 600 g of broth, another has two kinds
    of egg in it. Per-row queries would be 25 round trips to say so.
    """
    if not food_ids:
        return {}

    grouped: dict[int, list[str]] = {}
    rows = db.execute(
        select(FoodComponent.food_id, FoodComponent.name_vi)
        .where(FoodComponent.food_id.in_(food_ids))
        .order_by(FoodComponent.food_id, FoodComponent.sort_order)
    ).all()
    for food_id, name in rows:
        names = grouped.setdefault(food_id, [])
        if len(names) < SUMMARY_COMPONENTS:
            names.append(name)
    return grouped


@router.get("", response_model=Page[FoodSummary])
def list_foods(
    db: DbSession,
    search: str | None = Query(None, description="Match against the food name"),
    basis: str | None = Query(
        None, pattern="^(per_100g|per_serving)$",
        description="per_serving for cooked dishes, per_100g for ingredients",
    ),
    language: str = Query(DEFAULT_LANG, min_length=2, max_length=5),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> Page[FoodSummary]:
    """Search the catalog.

    Diacritics are stripped on both sides of `search`, so "banh mi" finds
    "bánh mì" - typing Vietnamese accents on a phone while hungry is not
    something to require. `name_ascii` is precomputed at seed time so this
    does not run unaccent() over 4124 rows per keystroke; it falls back to
    unaccent() on the raw name for the few rows that have none.

    `basis` is a filter and therefore exact, never fuzzy.
    """
    names = _name_for(language)

    conditions = []
    ranking = [names.c.name]
    if search:
        term = search.strip()
        needle = f"%{term}%"
        conditions.append(
            or_(
                names.c.name_ascii.ilike(func.unaccent(needle)),
                func.unaccent(names.c.name).ilike(func.unaccent(needle)),
            )
        )

        # Alphabetical order alone buries the obvious answer. Unaccented,
        # "pho" is a substring of "phô mai", "bánh phồng" and "Hải Phòng", and
        # sorting by name put all of those above "Phở" - the one dish the user
        # was plainly reaching for.
        #
        # So: names that *start* with the term first, then the shortest, then
        # alphabetical. Short wins because the plain dish is the short name and
        # its variants are the long ones - "Phở bò" before "Phở bò sốt vang,
        # bánh quẩy".
        prefix = func.unaccent(f"{term}%")
        ranking = [
            case(
                (names.c.name_ascii.ilike(prefix), 0),
                (func.unaccent(names.c.name).ilike(prefix), 0),
                else_=1,
            ),
            func.length(names.c.name),
            names.c.name,
        ]

    if basis:
        conditions.append(Food.basis == basis)

    # One row per (name, basis). 289 dish names repeat across the catalog -
    # the Institute surveyed "Phở gà" in three places and "Cháo lòng" in nine -
    # and a list showing all of them is a wall of rows the user cannot choose
    # between.
    #
    # Deduped on `basis` as well as name because "Phở gà" the cooked bowl and
    # "Phở gà" the per-100 g ingredient are different things, measured
    # differently; collapsing them would hide one of them entirely.
    #
    # **Which duplicate survives is not arbitrary.** Taking the lowest food_id
    # outright picked, for "Cháo lòng", the one row of the nine whose label was
    # never read - no ingredients, no serving weight - which is exactly the row
    # the user can do least with. So the completest record wins first: one with
    # a breakdown, then one with a serving weight, and only then the earliest
    # id as the tie-break.
    #
    # Chosen inside a subquery rather than with DISTINCT ON on the main query,
    # because DISTINCT ON dictates the sort order and the list is sorted by
    # search relevance, not by name.
    component_count = (
        select(func.count())
        .select_from(FoodComponent)
        .where(FoodComponent.food_id == Food.food_id)
        .correlate(Food)
        .scalar_subquery()
    )
    representatives = (
        select(Food.food_id)
        .join(names, names.c.food_id == Food.food_id)
        .where(*conditions)
        .distinct(func.lower(names.c.name), Food.basis)
        .order_by(
            func.lower(names.c.name),
            Food.basis,
            (component_count > 0).desc(),
            Food.serving_grams.is_(None),
            Food.food_id,
        )
        .subquery()
    )
    conditions = [*conditions, Food.food_id.in_(select(representatives.c.food_id))]

    base = (
        select(Food, names.c.name, FoodCategory.name_vi)
        .join(names, names.c.food_id == Food.food_id)
        .outerjoin(FoodCategory, FoodCategory.food_category_id == Food.food_category_id)
        .where(*conditions)
    )

    total = db.scalar(
        select(func.count())
        .select_from(Food)
        .join(names, names.c.food_id == Food.food_id)
        .where(*conditions)
    )

    rows = db.execute(
        base.order_by(*ranking).limit(limit).offset((page - 1) * limit)
    ).all()

    components = _component_names(db, [row[0].food_id for row in rows])

    return Page[FoodSummary](
        items=[_to_summary(row, components.get(row[0].food_id)) for row in rows],
        page=page,
        limit=limit,
        total=total or 0,
    )


@router.get("/{food_id}", response_model=FoodDetail)
def get_food(
    food_id: int,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, min_length=2, max_length=5),
) -> FoodDetail:
    names = _name_for(language)

    row = db.execute(
        select(Food, names.c.name, FoodCategory.name_vi)
        .join(names, names.c.food_id == Food.food_id)
        .outerjoin(FoodCategory, FoodCategory.food_category_id == Food.food_category_id)
        .where(Food.food_id == food_id)
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Food not found"
        )

    food = row[0]
    return FoodDetail(
        **_to_summary(row).model_dump(),
        source=food.source,
        components=[
            FoodComponentRead(name_vi=_vietnamese_only(c.name_vi), grams=c.grams)
            for c in food.components
        ],
    )
