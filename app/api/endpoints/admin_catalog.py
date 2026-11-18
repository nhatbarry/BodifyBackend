"""Admin console: muscle groups, equipment, food categories, and the option
lists the exercise and food forms pick from.

Every route requires an admin at aal2 holding the matching permission:
muscle groups and equipment are part of `exercises.*`, food categories of
`foods.*`. These are small
tables (tens of rows), so lists are returned whole, not paged.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import DbSession, requires
from app.models.equipment import Equipment
from app.models.exercise_equipment import ExerciseEquipment
from app.models.exercise_muscles import ExerciseMuscle
from app.models.exercises import Exercise
from app.models.foods import Food, FoodCategory
from app.models.muscle_groups import MuscleGroup
from app.models.term_translations import TermTranslation
from app.schemas.admin_catalog import (
    CatalogOptions,
    EquipmentRead,
    EquipmentWrite,
    FoodCategoryRead,
    FoodCategoryWrite,
    MuscleGroupRead,
    MuscleGroupWrite,
    OptionItem,
    TermTranslationIn,
    TermTranslationRead,
)
from app.services.admin_common import committing, conflict, not_found

router = APIRouter(prefix="/admin", tags=["admin-catalog"])

ExercisesView = requires("exercises.view")
ExercisesCreate = requires("exercises.create")
ExercisesUpdate = requires("exercises.update")
ExercisesDelete = requires("exercises.delete")
FoodsView = requires("foods.view")
FoodsCreate = requires("foods.create")
FoodsUpdate = requires("foods.update")
FoodsDelete = requires("foods.delete")
# The exercise form and the food form both pick from this list.
CatalogReader = requires("exercises.view", "foods.view")

TRACKING_TYPES = ["reps", "duration", "distance", "reps_duration"]
MEDIA_TYPES = ["thumbnail", "icon", "video", "image"]


# ---------------------------------------------------------------- helpers


def _translations(db: Session, column, ids: list[int]) -> dict[int, list[TermTranslationRead]]:
    grouped: dict[int, list[TermTranslationRead]] = {}
    if not ids:
        return grouped
    rows = db.execute(
        select(column, TermTranslation.language, TermTranslation.name, TermTranslation.description)
        .where(column.in_(ids))
        .order_by(column, TermTranslation.language)
    ).all()
    for owner_id, language, name, description in rows:
        grouped.setdefault(owner_id, []).append(
            TermTranslationRead(language=language, name=name, description=description)
        )
    return grouped


def _replace_translations(
    db: Session, *, muscle_id: int | None = None, equipment_id: int | None = None,
    items: list[TermTranslationIn],
) -> None:
    """The list sent is the whole set: languages left out are removed."""
    column = TermTranslation.muscle_id if muscle_id is not None else TermTranslation.equipment_id
    owner = muscle_id if muscle_id is not None else equipment_id
    existing = {
        t.language: t
        for t in db.scalars(select(TermTranslation).where(column == owner))
    }
    wanted = {t.language for t in items}
    for language, row in existing.items():
        if language not in wanted:
            db.delete(row)
    for item in items:
        row = existing.get(item.language)
        if row is None:
            db.add(TermTranslation(
                muscle_id=muscle_id, equipment_id=equipment_id,
                language=item.language, name=item.name, description=item.description,
            ))
        else:
            row.name = item.name
            row.description = item.description
            row.updated_at = func.now()


def _distinct_values(db: Session, column) -> list[str]:
    """A free-text column's values, most used first (the same ordering rule as
    the app's filters: alphabetical is language-dependent)."""
    rows = db.execute(
        select(column, func.count())
        .where(column.is_not(None), column != "")
        .group_by(column)
        .order_by(func.count().desc(), column)
    ).all()
    return [value for value, _ in rows]


# ---------------------------------------------------------------- options


@router.get("/catalog-options", response_model=CatalogOptions)
def catalog_options(_: CatalogReader, db: DbSession) -> CatalogOptions:
    def term_items(model, id_col, ts_col) -> list[OptionItem]:
        vi = select(ts_col.label("oid"), TermTranslation.name).where(
            TermTranslation.language == "vi", ts_col.is_not(None)
        ).subquery()
        en = select(ts_col.label("oid"), TermTranslation.name).where(
            TermTranslation.language == "en", ts_col.is_not(None)
        ).subquery()
        rows = db.execute(
            select(id_col, model.name, vi.c.name, en.c.name)
            .outerjoin(vi, vi.c.oid == id_col)
            .outerjoin(en, en.c.oid == id_col)
            .order_by(model.name)
        ).all()
        return [OptionItem(id=i, name=n, name_vi=v, name_en=e) for i, n, v, e in rows]

    categories = db.execute(
        select(FoodCategory.food_category_id, FoodCategory.name_vi, FoodCategory.name_en)
        .order_by(FoodCategory.name_vi)
    ).all()

    return CatalogOptions(
        muscle_groups=term_items(MuscleGroup, MuscleGroup.muscle_id, TermTranslation.muscle_id),
        equipment=term_items(Equipment, Equipment.equipment_id, TermTranslation.equipment_id),
        food_categories=[
            OptionItem(id=i, name=v, name_vi=v, name_en=e) for i, v, e in categories
        ],
        exercise_types=_distinct_values(db, Exercise.exercise_type),
        mechanics=_distinct_values(db, Exercise.mechanics),
        force_types=_distinct_values(db, Exercise.force_type),
        experience_levels=_distinct_values(db, Exercise.experience_level),
        tracking_types=TRACKING_TYPES,
        ai_algorithms=_distinct_values(db, Exercise.ai_algorithm),
        media_types=MEDIA_TYPES,
        food_sources=_distinct_values(db, Food.source),
    )


# ---------------------------------------------------------------- muscle groups


def _muscle_reads(db: Session, where=None) -> list[MuscleGroupRead]:
    parent = MuscleGroup.__table__.alias("parent")
    usage = (
        select(ExerciseMuscle.muscle_id, func.count().label("n"))
        .group_by(ExerciseMuscle.muscle_id).subquery()
    )
    children = (
        select(MuscleGroup.parent_id.label("pid"), func.count().label("n"))
        .where(MuscleGroup.parent_id.is_not(None))
        .group_by(MuscleGroup.parent_id).subquery()
    )
    query = (
        select(MuscleGroup, parent.c.name, func.coalesce(usage.c.n, 0), func.coalesce(children.c.n, 0))
        .outerjoin(parent, parent.c.muscle_id == MuscleGroup.parent_id)
        .outerjoin(usage, usage.c.muscle_id == MuscleGroup.muscle_id)
        .outerjoin(children, children.c.pid == MuscleGroup.muscle_id)
        .order_by(func.coalesce(usage.c.n, 0).desc(), MuscleGroup.name)
    )
    if where is not None:
        query = query.where(where)
    rows = db.execute(query).all()
    translations = _translations(db, TermTranslation.muscle_id, [r[0].muscle_id for r in rows])
    return [
        MuscleGroupRead(
            muscle_id=m.muscle_id, name=m.name, parent_id=m.parent_id, parent_name=parent_name,
            description=m.description, translations=translations.get(m.muscle_id, []),
            exercise_count=used, child_count=kids,
        )
        for m, parent_name, used, kids in rows
    ]


def _muscle_read(db: Session, muscle_id: int) -> MuscleGroupRead:
    found = _muscle_reads(db, MuscleGroup.muscle_id == muscle_id)
    if not found:
        raise not_found("nhóm cơ")
    return found[0]


def _check_parent(db: Session, muscle_id: int | None, parent_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == muscle_id:
        raise conflict("Nhóm cơ không thể là cha của chính nó")
    # Walk up from the proposed parent; meeting this group means a cycle.
    seen: set[int] = set()
    current: int | None = parent_id
    while current is not None:
        if current in seen:
            break
        seen.add(current)
        row = db.get(MuscleGroup, current)
        if row is None:
            raise conflict("Nhóm cơ cha không tồn tại")
        if muscle_id is not None and row.parent_id == muscle_id:
            raise conflict("Nhóm cha đã chọn đang nằm bên trong nhóm này, sẽ tạo vòng lặp")
        current = row.parent_id


@router.get("/muscle-groups", response_model=list[MuscleGroupRead])
def list_muscle_groups(_: ExercisesView, db: DbSession) -> list[MuscleGroupRead]:
    return _muscle_reads(db)


@router.get("/muscle-groups/{muscle_id}", response_model=MuscleGroupRead)
def get_muscle_group(muscle_id: int, _: ExercisesView, db: DbSession) -> MuscleGroupRead:
    return _muscle_read(db, muscle_id)


@router.post("/muscle-groups", response_model=MuscleGroupRead, status_code=status.HTTP_201_CREATED)
def create_muscle_group(payload: MuscleGroupWrite, _: ExercisesCreate, db: DbSession) -> MuscleGroupRead:
    _check_parent(db, None, payload.parent_id)
    with committing(db):
        row = MuscleGroup(name=payload.name, parent_id=payload.parent_id, description=payload.description)
        db.add(row)
        db.flush()
        _replace_translations(db, muscle_id=row.muscle_id, items=payload.translations)
    return _muscle_read(db, row.muscle_id)


@router.put("/muscle-groups/{muscle_id}", response_model=MuscleGroupRead)
def update_muscle_group(
    muscle_id: int, payload: MuscleGroupWrite, _: ExercisesUpdate, db: DbSession
) -> MuscleGroupRead:
    row = db.get(MuscleGroup, muscle_id)
    if row is None:
        raise not_found("nhóm cơ")
    _check_parent(db, muscle_id, payload.parent_id)
    with committing(db):
        row.name = payload.name
        row.parent_id = payload.parent_id
        row.description = payload.description
        _replace_translations(db, muscle_id=muscle_id, items=payload.translations)
    return _muscle_read(db, muscle_id)


@router.delete("/muscle-groups/{muscle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_muscle_group(muscle_id: int, _: ExercisesDelete, db: DbSession) -> Response:
    """Refused while in use. The foreign key from exercise_muscles cascades,
    so deleting would silently strip the muscle from every exercise."""
    current = _muscle_read(db, muscle_id)
    if current.exercise_count:
        raise conflict(f"Đang được gán cho {current.exercise_count} bài tập; hãy gỡ khỏi các bài tập đó trước")
    if current.child_count:
        raise conflict(f"Đang là nhóm cha của {current.child_count} nhóm khác; hãy chuyển các nhóm đó trước")
    with committing(db):
        db.execute(delete(MuscleGroup).where(MuscleGroup.muscle_id == muscle_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- equipment


def _equipment_reads(db: Session, where=None) -> list[EquipmentRead]:
    usage = (
        select(ExerciseEquipment.equipment_id, func.count().label("n"))
        .group_by(ExerciseEquipment.equipment_id).subquery()
    )
    query = (
        select(Equipment, func.coalesce(usage.c.n, 0))
        .outerjoin(usage, usage.c.equipment_id == Equipment.equipment_id)
        .order_by(func.coalesce(usage.c.n, 0).desc(), Equipment.name)
    )
    if where is not None:
        query = query.where(where)
    rows = db.execute(query).all()
    translations = _translations(db, TermTranslation.equipment_id, [r[0].equipment_id for r in rows])
    return [
        EquipmentRead(
            equipment_id=e.equipment_id, name=e.name, category=e.category,
            description=e.description, translations=translations.get(e.equipment_id, []),
            exercise_count=used,
        )
        for e, used in rows
    ]


def _equipment_read(db: Session, equipment_id: int) -> EquipmentRead:
    found = _equipment_reads(db, Equipment.equipment_id == equipment_id)
    if not found:
        raise not_found("thiết bị")
    return found[0]


@router.get("/equipment", response_model=list[EquipmentRead])
def list_equipment(_: ExercisesView, db: DbSession) -> list[EquipmentRead]:
    return _equipment_reads(db)


@router.get("/equipment/{equipment_id}", response_model=EquipmentRead)
def get_equipment(equipment_id: int, _: ExercisesView, db: DbSession) -> EquipmentRead:
    return _equipment_read(db, equipment_id)


@router.post("/equipment", response_model=EquipmentRead, status_code=status.HTTP_201_CREATED)
def create_equipment(payload: EquipmentWrite, _: ExercisesCreate, db: DbSession) -> EquipmentRead:
    with committing(db):
        row = Equipment(name=payload.name, category=payload.category, description=payload.description)
        db.add(row)
        db.flush()
        _replace_translations(db, equipment_id=row.equipment_id, items=payload.translations)
    return _equipment_read(db, row.equipment_id)


@router.put("/equipment/{equipment_id}", response_model=EquipmentRead)
def update_equipment(
    equipment_id: int, payload: EquipmentWrite, _: ExercisesUpdate, db: DbSession
) -> EquipmentRead:
    row = db.get(Equipment, equipment_id)
    if row is None:
        raise not_found("thiết bị")
    with committing(db):
        row.name = payload.name
        row.category = payload.category
        row.description = payload.description
        _replace_translations(db, equipment_id=equipment_id, items=payload.translations)
    return _equipment_read(db, equipment_id)


@router.delete("/equipment/{equipment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_equipment(equipment_id: int, _: ExercisesDelete, db: DbSession) -> Response:
    current = _equipment_read(db, equipment_id)
    if current.exercise_count:
        raise conflict(f"Đang được gán cho {current.exercise_count} bài tập; hãy gỡ khỏi các bài tập đó trước")
    with committing(db):
        db.execute(delete(Equipment).where(Equipment.equipment_id == equipment_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- food categories


def _category_reads(db: Session, where=None) -> list[FoodCategoryRead]:
    usage = (
        select(Food.food_category_id, func.count().label("n"))
        .group_by(Food.food_category_id).subquery()
    )
    query = (
        select(FoodCategory, func.coalesce(usage.c.n, 0))
        .outerjoin(usage, usage.c.food_category_id == FoodCategory.food_category_id)
        .order_by(FoodCategory.source, FoodCategory.source_code)
    )
    if where is not None:
        query = query.where(where)
    return [
        FoodCategoryRead(
            food_category_id=c.food_category_id, source=c.source, source_code=c.source_code,
            name_vi=c.name_vi, name_en=c.name_en, food_count=n,
        )
        for c, n in db.execute(query).all()
    ]


def _category_read(db: Session, category_id: int) -> FoodCategoryRead:
    found = _category_reads(db, FoodCategory.food_category_id == category_id)
    if not found:
        raise not_found("nhóm thực phẩm")
    return found[0]


@router.get("/food-categories", response_model=list[FoodCategoryRead])
def list_food_categories(_: FoodsView, db: DbSession) -> list[FoodCategoryRead]:
    return _category_reads(db)


@router.get("/food-categories/{category_id}", response_model=FoodCategoryRead)
def get_food_category(category_id: int, _: FoodsView, db: DbSession) -> FoodCategoryRead:
    return _category_read(db, category_id)


@router.post("/food-categories", response_model=FoodCategoryRead, status_code=status.HTTP_201_CREATED)
def create_food_category(payload: FoodCategoryWrite, _: FoodsCreate, db: DbSession) -> FoodCategoryRead:
    with committing(db):
        row = FoodCategory(**payload.model_dump())
        db.add(row)
        db.flush()
    return _category_read(db, row.food_category_id)


@router.put("/food-categories/{category_id}", response_model=FoodCategoryRead)
def update_food_category(
    category_id: int, payload: FoodCategoryWrite, _: FoodsUpdate, db: DbSession
) -> FoodCategoryRead:
    row = db.get(FoodCategory, category_id)
    if row is None:
        raise not_found("nhóm thực phẩm")
    with committing(db):
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
    return _category_read(db, category_id)


@router.delete("/food-categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_food_category(category_id: int, _: FoodsDelete, db: DbSession) -> Response:
    """Foods in it stay; their category becomes empty (ON DELETE SET NULL)."""
    _category_read(db, category_id)
    with committing(db):
        db.execute(delete(FoodCategory).where(FoodCategory.food_category_id == category_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

