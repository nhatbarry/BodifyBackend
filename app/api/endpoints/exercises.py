from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import case, exists, func, or_, select

from app.api.dependencies import DbSession
from app.models.equipment import Equipment
from app.models.exercise_equipment import ExerciseEquipment
from app.models.exercise_media import ExerciseMedia
from app.models.exercise_muscles import ExerciseMuscle
from app.models.exercise_translations import ExerciseTranslation
from app.models.exercises import Exercise
from app.models.muscle_groups import MuscleGroup
from app.models.term_translations import TermTranslation
from app.schemas.common import Page
from app.schemas.exercise_catalog import (
    ExerciseDetail,
    ExerciseEquipmentItem,
    ExerciseFilters,
    ExerciseListItem,
    ExerciseMediaUrls,
    ExerciseMuscleItem,
    FilterOption,
)
from app.services.storage import presigned_url

router = APIRouter(prefix="/exercises", tags=["exercises"])

DEFAULT_LANG = "vi"


def _best_translation(language: str):
    """One translation row per exercise: requested language, else English, else any.

    Exercise names exist only in `exercise_translations`, so without this every
    listed row would be nameless.
    """
    preference = case(
        (ExerciseTranslation.language == language, 0),
        (ExerciseTranslation.language == "en", 1),
        else_=2,
    )
    ranked = select(
        ExerciseTranslation.exercise_id.label("exercise_id"),
        ExerciseTranslation.language.label("language"),
        ExerciseTranslation.name.label("name"),
        ExerciseTranslation.overview.label("overview"),
        ExerciseTranslation.instructions.label("instructions"),
        ExerciseTranslation.tips.label("tips"),
        func.row_number()
        .over(
            partition_by=ExerciseTranslation.exercise_id,
            order_by=(preference.asc(), ExerciseTranslation.language.asc()),
        )
        .label("rn"),
    ).subquery()

    return select(ranked).where(ranked.c.rn == 1).subquery()


def _url_or_none(storage_key: str | None) -> str | None:
    return presigned_url(storage_key) if storage_key else None


def _translated_name(base_column, translation_name):
    """Translated label when one exists, else the English name in the base table."""
    return func.coalesce(translation_name, base_column)


def _muscle_translation(language: str):
    """Vietnamese (or other) names for muscle groups, one row per muscle."""
    return (
        select(
            TermTranslation.muscle_id.label("muscle_id"),
            TermTranslation.name.label("name"),
        )
        .where(
            TermTranslation.muscle_id.is_not(None),
            TermTranslation.language == language,
        )
        .subquery()
    )


def _equipment_translation(language: str):
    return (
        select(
            TermTranslation.equipment_id.label("equipment_id"),
            TermTranslation.name.label("name"),
        )
        .where(
            TermTranslation.equipment_id.is_not(None),
            TermTranslation.language == language,
        )
        .subquery()
    )


# Declared before /{exercise_id}: FastAPI matches in order, and "filters" would
# otherwise be parsed as an exercise id and rejected as a bad integer.
@router.get("/filters", response_model=ExerciseFilters)
def get_exercise_filters(
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> ExerciseFilters:
    """Filter options for the explorer UI, derived from the data itself."""

    def distinct_values(column) -> list[str]:
        rows = db.execute(
            select(column).where(column.is_not(None)).distinct().order_by(column)
        ).scalars().all()
        return [value for value in rows if value]

    # Ordered by how many exercises actually use each term, not alphabetically.
    # Alphabetical order is language-dependent - in Vietnamese nearly every
    # muscle starts with "Cơ", which buried the common ones behind
    # "Cân gan chân" and friends - while usage order is the same useful list in
    # any language.
    mt = _muscle_translation(language)
    muscle_label = _translated_name(MuscleGroup.name, mt.c.name)
    muscle_usage = (
        select(func.count())
        .select_from(ExerciseMuscle)
        .where(ExerciseMuscle.muscle_id == MuscleGroup.muscle_id)
        .scalar_subquery()
    )
    muscle_groups = db.execute(
        select(MuscleGroup.muscle_id, muscle_label)
        .join(mt, mt.c.muscle_id == MuscleGroup.muscle_id, isouter=True)
        # "None" is an import placeholder for "no secondary muscle", not a
        # muscle anyone would filter by.
        .where(MuscleGroup.name != "None")
        .order_by(muscle_usage.desc(), muscle_label)
    ).all()

    et = _equipment_translation(language)
    equipment_label = _translated_name(Equipment.name, et.c.name)
    equipment_usage = (
        select(func.count())
        .select_from(ExerciseEquipment)
        .where(ExerciseEquipment.equipment_id == Equipment.equipment_id)
        .scalar_subquery()
    )
    equipment = db.execute(
        select(Equipment.equipment_id, equipment_label)
        .join(et, et.c.equipment_id == Equipment.equipment_id, isouter=True)
        .order_by(equipment_usage.desc(), equipment_label)
    ).all()

    return ExerciseFilters(
        muscle_groups=[FilterOption(id=i, name=n) for i, n in muscle_groups],
        equipment=[FilterOption(id=i, name=n) for i, n in equipment],
        experience_levels=distinct_values(Exercise.experience_level),
        exercise_types=distinct_values(Exercise.exercise_type),
        mechanics=distinct_values(Exercise.mechanics),
        force_types=distinct_values(Exercise.force_type),
    )


@router.get("", response_model=Page[ExerciseListItem])
def list_exercises(
    db: DbSession,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    language: str = Query(DEFAULT_LANG, max_length=10),
    search: str | None = Query(None, description="Match against the exercise name"),
    muscle_group: str | None = Query(None, description="Muscle group id or name"),
    equipment: str | None = Query(None, description="Equipment id or name"),
    experience_level: str | None = None,
    exercise_type: str | None = None,
    mechanics: str | None = None,
    force_type: str | None = None,
) -> Page[ExerciseListItem]:
    """Exercise explorer: search, filter and pagination in one endpoint."""
    translation = _best_translation(language)

    conditions = []
    if search:
        # unaccent() on both sides so "nguc" finds "Ngực" - Vietnamese users
        # routinely type without diacritics. ILIKE covers the casing.
        conditions.append(
            func.unaccent(translation.c.name).ilike(func.unaccent(f"%{search}%"))
        )

    # These columns hold display text ("Strength", "Beginner"), so match without
    # forcing the caller to know the exact casing.
    for value, column in (
        (experience_level, Exercise.experience_level),
        (exercise_type, Exercise.exercise_type),
        (mechanics, Exercise.mechanics),
        (force_type, Exercise.force_type),
    ):
        if value:
            conditions.append(func.lower(column) == value.lower())

    # Name matching accepts any language: the term_translations join means
    # ?muscle_group=Chest and ?muscle_group=Cơ ngực select the same exercises.
    # Being inside EXISTS, a term with several translations still yields one
    # row in the outer query.
    #
    # Diacritics are NOT stripped here, unlike in `search` above. "Tạ đòn"
    # (barbell) and "Tạ đơn" (dumbbell) both unaccent to "ta don", so an
    # accent-insensitive filter would silently merge two different pieces of
    # equipment. A filter has to be exact; only free-text search may be fuzzy.
    if muscle_group:
        link = ExerciseMuscle.exercise_id == Exercise.exercise_id
        if muscle_group.isdigit():
            conditions.append(
                exists().where(link, ExerciseMuscle.muscle_id == int(muscle_group))
            )
        else:
            wanted = muscle_group.lower()
            conditions.append(
                exists()
                .where(link, ExerciseMuscle.muscle_id == MuscleGroup.muscle_id)
                .where(
                    or_(
                        func.lower(MuscleGroup.name) == wanted,
                        exists()
                        .where(TermTranslation.muscle_id == MuscleGroup.muscle_id)
                        .where(func.lower(TermTranslation.name) == wanted),
                    )
                )
            )

    if equipment:
        link = ExerciseEquipment.exercise_id == Exercise.exercise_id
        if equipment.isdigit():
            conditions.append(
                exists().where(link, ExerciseEquipment.equipment_id == int(equipment))
            )
        else:
            wanted = equipment.lower()
            conditions.append(
                exists()
                .where(link, ExerciseEquipment.equipment_id == Equipment.equipment_id)
                .where(
                    or_(
                        func.lower(Equipment.name) == wanted,
                        exists()
                        .where(TermTranslation.equipment_id == Equipment.equipment_id)
                        .where(func.lower(TermTranslation.name) == wanted),
                    )
                )
            )

    total = db.scalar(
        select(func.count())
        .select_from(Exercise)
        .join(translation, translation.c.exercise_id == Exercise.exercise_id, isouter=True)
        .where(*conditions)
    )

    offset = (page - 1) * limit
    rows = db.execute(
        select(Exercise, translation.c.name, translation.c.language)
        .join(translation, translation.c.exercise_id == Exercise.exercise_id, isouter=True)
        .where(*conditions)
        .order_by(Exercise.exercise_id)
        .limit(limit)
        .offset(offset)
    ).all()

    # Thumbnails in a separate query: joining media here would duplicate rows
    # for exercises that hold several files.
    thumbnails: dict[int, str] = {}
    if rows:
        ids = [row[0].exercise_id for row in rows]
        for exercise_id, storage_key in db.execute(
            select(ExerciseMedia.exercise_id, ExerciseMedia.storage_key)
            .where(
                ExerciseMedia.exercise_id.in_(ids),
                ExerciseMedia.media_type == "thumbnail",
            )
            .order_by(ExerciseMedia.exercise_id, ExerciseMedia.sort_order)
        ).all():
            thumbnails.setdefault(exercise_id, storage_key)

    items = [
        ExerciseListItem(
            exercise_id=exercise.exercise_id,
            name=name,
            language=translation_language,
            thumbnail_url=_url_or_none(thumbnails.get(exercise.exercise_id)),
            exercise_type=exercise.exercise_type,
            mechanics=exercise.mechanics,
            force_type=exercise.force_type,
            experience_level=exercise.experience_level,
        )
        for exercise, name, translation_language in rows
    ]

    return Page[ExerciseListItem](
        items=items, page=page, limit=limit, total=total or 0
    )


@router.get("/{exercise_id}", response_model=ExerciseDetail)
def get_exercise(
    exercise_id: int,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> ExerciseDetail:
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found"
        )

    best = _best_translation(language)
    translation = db.execute(
        select(best).where(best.c.exercise_id == exercise_id)
    ).one_or_none()

    mt = _muscle_translation(language)
    muscle_label = _translated_name(MuscleGroup.name, mt.c.name)
    muscles = db.execute(
        select(MuscleGroup.muscle_id, muscle_label, ExerciseMuscle.role)
        .join(ExerciseMuscle, ExerciseMuscle.muscle_id == MuscleGroup.muscle_id)
        .join(mt, mt.c.muscle_id == MuscleGroup.muscle_id, isouter=True)
        .where(
            ExerciseMuscle.exercise_id == exercise_id,
            # Import placeholder standing in for "no secondary muscle".
            MuscleGroup.name != "None",
        )
        # 'primary' sorts before 'secondary', which is the order to display them
        .order_by(ExerciseMuscle.role, muscle_label)
    ).all()

    et = _equipment_translation(language)
    equipment_label = _translated_name(Equipment.name, et.c.name)
    equipment = db.execute(
        select(Equipment.equipment_id, equipment_label)
        .join(ExerciseEquipment, ExerciseEquipment.equipment_id == Equipment.equipment_id)
        .join(et, et.c.equipment_id == Equipment.equipment_id, isouter=True)
        .where(ExerciseEquipment.exercise_id == exercise_id)
        .order_by(equipment_label)
    ).all()

    media_rows = db.execute(
        select(ExerciseMedia.media_type, ExerciseMedia.storage_key)
        .where(ExerciseMedia.exercise_id == exercise_id)
        .order_by(ExerciseMedia.sort_order, ExerciseMedia.media_id)
    ).all()
    first_of: dict[str, str] = {}
    for media_type, storage_key in media_rows:
        first_of.setdefault(media_type, storage_key)

    return ExerciseDetail(
        exercise_id=exercise.exercise_id,
        name=translation.name if translation else None,
        language=translation.language if translation else None,
        # A translation row can exist with some fields empty, so fall back per
        # field rather than showing nothing.
        overview=(translation.overview if translation else None) or exercise.overview,
        instructions=(translation.instructions if translation else None) or exercise.instructions,
        tips=(translation.tips if translation else None) or exercise.tips,
        exercise_type=exercise.exercise_type,
        mechanics=exercise.mechanics,
        force_type=exercise.force_type,
        experience_level=exercise.experience_level,
        muscles=[
            ExerciseMuscleItem(id=muscle_id, name=name, role=role)
            for muscle_id, name, role in muscles
        ],
        equipment=[
            ExerciseEquipmentItem(id=equipment_id, name=name)
            for equipment_id, name in equipment
        ],
        media=ExerciseMediaUrls(
            thumbnail_url=_url_or_none(first_of.get("thumbnail")),
            video_url=_url_or_none(first_of.get("video")),
        ),
    )
