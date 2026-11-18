"""Admin console: the food catalog the diary logs against.

Every route requires an admin at aal2. Diary entries copy a food's name and
nutrition when they are logged, so editing or deleting a catalog row never
rewrites what somebody already ate; deleting only unlinks those entries
(`meal_items.food_id` is ON DELETE SET NULL).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.dependencies import DbSession, requires
from app.models.foods import Food, FoodCategory, FoodComponent, FoodTranslation
from app.models.meals import MealItem
from app.schemas.admin_foods import (
    Basis,
    FoodAdminDetail,
    FoodAdminSummary,
    FoodComponentRead,
    FoodTranslationRead,
    FoodUsage,
    FoodWrite,
    Visibility,
)
from app.schemas.common import Page
from app.services import food_visibility
from app.services.admin_common import committing, conflict, not_found
from app.services.storage import presigned_url

router = APIRouter(prefix="/admin/foods", tags=["admin-foods"])

FoodsView = requires("foods.view")
FoodsCreate = requires("foods.create")
FoodsUpdate = requires("foods.update")
FoodsDelete = requires("foods.delete")


def _visibility(food: Food) -> Visibility:
    if food.hidden_override is None:
        return "auto"
    return "hidden" if food.hidden_override else "shown"


def _name(language: str):
    return (
        select(FoodTranslation.name)
        .where(FoodTranslation.food_id == Food.food_id, FoodTranslation.language_code == language)
        .scalar_subquery()
    )


def _image(key: str | None) -> str | None:
    return presigned_url(key) if key else None


@router.get("", response_model=Page[FoodAdminSummary])
def list_foods(
    _: FoodsView,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    basis: Basis | None = None,
    category_id: int | None = None,
    source: Annotated[str | None, Query(max_length=16)] = None,
    shown: Annotated[Literal["all", "shown", "hidden"], Query()] = "all",
    overridden: bool | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[FoodAdminSummary]:
    conditions = []
    term = (search or "").strip()
    if term:
        needle = f"%{term}%"
        name_match = select(FoodTranslation.food_id).where(
            or_(
                FoodTranslation.name_ascii.ilike(func.unaccent(needle)),
                func.unaccent(FoodTranslation.name).ilike(func.unaccent(needle)),
            )
        )
        alternatives = [Food.food_id.in_(name_match), Food.source_code.ilike(needle)]
        if term.isdigit():
            alternatives.append(Food.food_id == int(term))
        conditions.append(or_(*alternatives))
    if basis is not None:
        conditions.append(Food.basis == basis)
    if category_id is not None:
        conditions.append(Food.food_category_id == category_id)
    if source:
        conditions.append(Food.source == source)
    if shown != "all":
        conditions.append(Food.is_hidden.is_(shown == "hidden"))
    if overridden is not None:
        conditions.append(
            Food.hidden_override.is_not(None) if overridden else Food.hidden_override.is_(None)
        )

    total = db.scalar(select(func.count()).select_from(Food).where(*conditions)) or 0
    name_vi = _name("vi").label("name_vi")
    name_en = _name("en").label("name_en")
    components = (
        select(func.count()).where(FoodComponent.food_id == Food.food_id).scalar_subquery()
    )
    rows = db.execute(
        select(Food, name_vi, name_en, FoodCategory.name_vi, components)
        .outerjoin(FoodCategory, FoodCategory.food_category_id == Food.food_category_id)
        .where(*conditions)
        .order_by(name_vi.asc().nulls_last(), Food.food_id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()

    return Page(
        items=[
            FoodAdminSummary(
                food_id=f.food_id, name_vi=vi, name_en=en, source=f.source,
                source_code=f.source_code, basis=f.basis, energy_kcal=f.energy_kcal,
                protein_g=f.protein_g, fat_g=f.fat_g, carb_g=f.carb_g,
                serving_grams=f.serving_grams, category_name=category,
                is_hidden=f.is_hidden, visibility=_visibility(f),
                image_url=_image(f.image_key), component_count=n_components,
                updated_at=f.updated_at,
            )
            for f, vi, en, category, n_components in rows
        ],
        page=page, limit=limit, total=total,
    )


def _detail(db: Session, food_id: int) -> FoodAdminDetail:
    food = db.get(Food, food_id)
    if food is None:
        raise not_found("thực phẩm")
    db.refresh(food)
    category = db.get(FoodCategory, food.food_category_id) if food.food_category_id else None
    translations = db.scalars(
        select(FoodTranslation)
        .where(FoodTranslation.food_id == food_id)
        .order_by(FoodTranslation.language_code.desc())  # vi before en
    ).all()
    components = db.scalars(
        select(FoodComponent)
        .where(FoodComponent.food_id == food_id)
        .order_by(FoodComponent.sort_order, FoodComponent.food_component_id)
    ).all()
    diary = db.scalar(select(func.count()).where(MealItem.food_id == food_id)) or 0
    return FoodAdminDetail(
        food_id=food.food_id, source=food.source, source_code=food.source_code,
        food_category_id=food.food_category_id,
        category_name=category.name_vi if category else None,
        basis=food.basis, serving_grams=food.serving_grams, serving_source=food.serving_source,
        energy_kcal=food.energy_kcal, protein_g=food.protein_g, fat_g=food.fat_g,
        carb_g=food.carb_g, fiber_g=food.fiber_g, nutrients=food.nutrients,
        image_key=food.image_key, image_url=_image(food.image_key),
        source_image_url=food.image_url,
        is_hidden=food.is_hidden, visibility=_visibility(food),
        translations=[FoodTranslationRead(language=t.language_code, name=t.name) for t in translations],
        components=[
            FoodComponentRead(name_vi=c.name_vi, grams=c.grams, source=c.source) for c in components
        ],
        usage=FoodUsage(diary_entries=diary),
        created_at=food.created_at, updated_at=food.updated_at,
    )


def _apply(db: Session, food: Food, payload: FoodWrite) -> None:
    if payload.food_category_id is not None and db.get(FoodCategory, payload.food_category_id) is None:
        raise conflict("Nhóm thực phẩm không tồn tại")

    serving_changed = food.serving_grams != payload.serving_grams
    food.source = payload.source
    food.source_code = payload.source_code
    food.food_category_id = payload.food_category_id
    food.basis = payload.basis
    food.serving_grams = payload.serving_grams
    if payload.serving_grams is None:
        food.serving_source = None
    elif serving_changed or food.serving_source is None:
        # Says where the number came from; a hand-typed one is ours.
        food.serving_source = "admin"
    food.energy_kcal = payload.energy_kcal
    food.protein_g = payload.protein_g
    food.fat_g = payload.fat_g
    food.carb_g = payload.carb_g
    food.fiber_g = payload.fiber_g
    food.nutrients = payload.nutrients
    food.image_key = payload.image_key
    food.hidden_override = {"auto": None, "shown": False, "hidden": True}[payload.visibility]
    if food.hidden_override is not None:
        food.is_hidden = food.hidden_override
    food.updated_at = func.now()
    fid = food.food_id

    existing = {
        t.language_code: t
        for t in db.scalars(select(FoodTranslation).where(FoodTranslation.food_id == fid))
    }
    wanted = {t.language for t in payload.translations}
    for language, row in existing.items():
        if language not in wanted:
            db.delete(row)
    for item in payload.translations:
        row = existing.get(item.language)
        if row is None:
            row = FoodTranslation(food_id=fid, language_code=item.language, name=item.name)
            db.add(row)
        row.name = item.name
        row.name_ascii = food_visibility.strip_accents(item.name)

    # Components are a printed list with no identity of their own: replaced whole.
    # A changed list is ours, not the label's.
    old = [
        (c.name_vi, c.grams, c.source)
        for c in db.scalars(
            select(FoodComponent).where(FoodComponent.food_id == fid)
            .order_by(FoodComponent.sort_order, FoodComponent.food_component_id)
        )
    ]
    new = [(c.name_vi, c.grams) for c in payload.components]
    if [(n, g) for n, g, _ in old] != new:
        old_source = {(n, g): s for n, g, s in old}
        db.execute(delete(FoodComponent).where(FoodComponent.food_id == fid))
        db.flush()
        for position, (name, grams) in enumerate(new):
            db.add(FoodComponent(
                food_id=fid, name_vi=name, grams=grams, sort_order=position,
                source=old_source.get((name, grams), "admin"),
            ))
    db.flush()
    food_visibility.recompute(db)


@router.get("/{food_id}", response_model=FoodAdminDetail)
def get_food(food_id: int, _: FoodsView, db: DbSession) -> FoodAdminDetail:
    return _detail(db, food_id)


@router.post("", response_model=FoodAdminDetail, status_code=status.HTTP_201_CREATED)
def create_food(payload: FoodWrite, _: FoodsCreate, db: DbSession) -> FoodAdminDetail:
    with committing(db):
        food = Food(source=payload.source, basis=payload.basis, energy_kcal=payload.energy_kcal)
        db.add(food)
        db.flush()
        _apply(db, food, payload)
    return _detail(db, food.food_id)


@router.put("/{food_id}", response_model=FoodAdminDetail)
def update_food(food_id: int, payload: FoodWrite, _: FoodsUpdate, db: DbSession) -> FoodAdminDetail:
    food = db.get(Food, food_id)
    if food is None:
        raise not_found("thực phẩm")
    with committing(db):
        _apply(db, food, payload)
    return _detail(db, food_id)


@router.delete("/{food_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_food(food_id: int, _: FoodsDelete, db: DbSession) -> Response:
    """Diary entries keep their name and figures and lose only the link, so
    they can no longer open the recipe or be rescaled in grams. Hiding the
    row (`visibility: hidden`) avoids that and is usually what is wanted."""
    if db.get(Food, food_id) is None:
        raise not_found("thực phẩm")
    with committing(db):
        db.execute(delete(Food).where(Food.food_id == food_id))
        db.flush()
        # The deleted row may have been the one shown for its name.
        food_visibility.recompute(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
