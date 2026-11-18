"""Admin console: the exercise catalog the app reads.

Every route requires an admin at aal2. Names, overviews, instructions and
tips live in `exercise_translations` (vi/en); the legacy columns of the same
name on `exercises` are left untouched, the app reads them only as a
per-field fallback.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.orm import Session

from app.api.dependencies import DbSession, requires
from app.models.equipment import Equipment
from app.models.exercise_equipment import ExerciseEquipment
from app.models.exercise_media import ExerciseMedia
from app.models.exercise_muscles import ExerciseMuscle
from app.models.exercise_translations import ExerciseTranslation
from app.models.exercises import Exercise
from app.models.muscle_groups import MuscleGroup
from app.models.term_translations import TermTranslation
from app.schemas.admin_exercises import (
    ExerciseAdminDetail,
    ExerciseAdminSummary,
    ExerciseEquipmentRead,
    ExerciseMediaRead,
    ExerciseMuscleRead,
    ExerciseTranslationRead,
    ExerciseUsage,
    ExerciseWrite,
    TrackingType,
)
from app.schemas.common import Page
from app.services.admin_common import committing, conflict, not_found
from app.services.exercise_media import list_image_urls, url_or_none

router = APIRouter(prefix="/admin/exercises", tags=["admin-exercises"])

ExercisesView = requires("exercises.view")
ExercisesCreate = requires("exercises.create")
ExercisesUpdate = requires("exercises.update")
ExercisesDelete = requires("exercises.delete")


def _label(model, id_col, ts_col):
    """`(id, label)` with the Vietnamese translation, else the canonical name."""
    vi = (
        select(ts_col.label("oid"), TermTranslation.name.label("name"))
        .where(TermTranslation.language == "vi", ts_col.is_not(None))
        .subquery()
    )
    return vi, func.coalesce(vi.c.name, model.name)


def _translation_name(language: str):
    return (
        select(ExerciseTranslation.name)
        .where(
            ExerciseTranslation.exercise_id == Exercise.exercise_id,
            ExerciseTranslation.language == language,
        )
        .scalar_subquery()
    )


def _usage(db: Session, exercise_id: int) -> ExerciseUsage:
    row = db.execute(
        text(
            """
            SELECT
              (SELECT count(*) FROM workout_exercises WHERE exercise_id = :id),
              (SELECT count(*) FROM session_exercises WHERE exercise_id = :id),
              (SELECT count(*) FROM personal_records WHERE exercise_id = :id)
                + (SELECT count(*) FROM personal_bests WHERE exercise_id = :id),
              (SELECT count(*) FROM achievements WHERE exercise_id = :id)
            """
        ),
        {"id": exercise_id},
    ).one()
    return ExerciseUsage(
        plan_exercises=row[0], session_exercises=row[1], personal_records=row[2], achievements=row[3]
    )


@router.get("", response_model=Page[ExerciseAdminSummary])
def list_exercises(
    _: ExercisesView,
    db: DbSession,
    search: Annotated[str | None, Query(max_length=100)] = None,
    muscle_id: int | None = None,
    equipment_id: int | None = None,
    tracking_type: TrackingType | None = None,
    camera: Annotated[bool | None, Query(description="Only rows the camera can count, or only rows it cannot")] = None,
    missing: Annotated[str | None, Query(pattern="^(vi|en|thumbnail|video|muscle)$")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Page[ExerciseAdminSummary]:
    conditions = []
    term = (search or "").strip()
    if term:
        if term.isdigit():
            conditions.append(Exercise.exercise_id == int(term))
        else:
            needle = func.unaccent(f"%{term}%")
            conditions.append(
                exists().where(
                    ExerciseTranslation.exercise_id == Exercise.exercise_id,
                    func.unaccent(ExerciseTranslation.name).ilike(needle),
                )
            )
    if muscle_id is not None:
        conditions.append(exists().where(
            ExerciseMuscle.exercise_id == Exercise.exercise_id, ExerciseMuscle.muscle_id == muscle_id
        ))
    if equipment_id is not None:
        conditions.append(exists().where(
            ExerciseEquipment.exercise_id == Exercise.exercise_id,
            ExerciseEquipment.equipment_id == equipment_id,
        ))
    if tracking_type is not None:
        conditions.append(Exercise.tracking_type == tracking_type)
    if camera is not None:
        conditions.append(Exercise.ai_algorithm.is_not(None) if camera else Exercise.ai_algorithm.is_(None))
    if missing in ("vi", "en"):
        conditions.append(~exists().where(
            ExerciseTranslation.exercise_id == Exercise.exercise_id,
            ExerciseTranslation.language == missing,
        ))
    elif missing in ("thumbnail", "video"):
        conditions.append(~exists().where(
            ExerciseMedia.exercise_id == Exercise.exercise_id, ExerciseMedia.media_type == missing
        ))
    elif missing == "muscle":
        conditions.append(~exists().where(ExerciseMuscle.exercise_id == Exercise.exercise_id))

    total = db.scalar(select(func.count()).select_from(Exercise).where(*conditions)) or 0
    name_vi = _translation_name("vi").label("name_vi")
    name_en = _translation_name("en").label("name_en")
    rows = db.execute(
        select(Exercise, name_vi, name_en)
        .where(*conditions)
        .order_by(func.coalesce(name_vi, name_en).asc().nulls_last(), Exercise.exercise_id)
        .offset((page - 1) * limit)
        .limit(limit)
    ).all()
    ids = [r[0].exercise_id for r in rows]

    muscles: dict[int, list[str]] = {}
    equipment: dict[int, list[str]] = {}
    if ids:
        vi, label = _label(MuscleGroup, MuscleGroup.muscle_id, TermTranslation.muscle_id)
        for exercise_id, name in db.execute(
            select(ExerciseMuscle.exercise_id, label)
            .join(MuscleGroup, MuscleGroup.muscle_id == ExerciseMuscle.muscle_id)
            .outerjoin(vi, vi.c.oid == MuscleGroup.muscle_id)
            .where(ExerciseMuscle.exercise_id.in_(ids), ExerciseMuscle.role == "primary")
            .order_by(label)
        ):
            muscles.setdefault(exercise_id, []).append(name)
        vi, label = _label(Equipment, Equipment.equipment_id, TermTranslation.equipment_id)
        for exercise_id, name in db.execute(
            select(ExerciseEquipment.exercise_id, label)
            .join(Equipment, Equipment.equipment_id == ExerciseEquipment.equipment_id)
            .outerjoin(vi, vi.c.oid == Equipment.equipment_id)
            .where(ExerciseEquipment.exercise_id.in_(ids))
            .order_by(label)
        ):
            equipment.setdefault(exercise_id, []).append(name)
    images = list_image_urls(db, ids)

    return Page(
        items=[
            ExerciseAdminSummary(
                exercise_id=e.exercise_id, name_vi=vi_name, name_en=en_name,
                exercise_type=e.exercise_type, tracking_type=e.tracking_type,
                experience_level=e.experience_level, ai_algorithm=e.ai_algorithm,
                primary_muscles=muscles.get(e.exercise_id, []),
                equipment=equipment.get(e.exercise_id, []),
                icon_url=(images.get(e.exercise_id, (None, None))[0]
                          or images.get(e.exercise_id, (None, None))[1]),
                updated_at=e.updated_at,
            )
            for e, vi_name, en_name in rows
        ],
        page=page, limit=limit, total=total,
    )


def _detail(db: Session, exercise_id: int) -> ExerciseAdminDetail:
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise not_found("bài tập")
    db.refresh(exercise)

    translations = db.scalars(
        select(ExerciseTranslation)
        .where(ExerciseTranslation.exercise_id == exercise_id)
        .order_by(ExerciseTranslation.language.desc())  # vi before en
    ).all()

    vi, label = _label(MuscleGroup, MuscleGroup.muscle_id, TermTranslation.muscle_id)
    muscles = db.execute(
        select(ExerciseMuscle.muscle_id, ExerciseMuscle.role, label)
        .join(MuscleGroup, MuscleGroup.muscle_id == ExerciseMuscle.muscle_id)
        .outerjoin(vi, vi.c.oid == MuscleGroup.muscle_id)
        .where(ExerciseMuscle.exercise_id == exercise_id)
        .order_by(ExerciseMuscle.role, label)
    ).all()

    vi, label = _label(Equipment, Equipment.equipment_id, TermTranslation.equipment_id)
    equipment = db.execute(
        select(ExerciseEquipment.equipment_id, label)
        .join(Equipment, Equipment.equipment_id == ExerciseEquipment.equipment_id)
        .outerjoin(vi, vi.c.oid == Equipment.equipment_id)
        .where(ExerciseEquipment.exercise_id == exercise_id)
        .order_by(label)
    ).all()

    media = db.scalars(
        select(ExerciseMedia)
        .where(ExerciseMedia.exercise_id == exercise_id)
        .order_by(ExerciseMedia.media_type, ExerciseMedia.sort_order, ExerciseMedia.media_id)
    ).all()

    return ExerciseAdminDetail(
        exercise_id=exercise.exercise_id,
        exercise_type=exercise.exercise_type,
        mechanics=exercise.mechanics,
        force_type=exercise.force_type,
        experience_level=exercise.experience_level,
        tracking_type=exercise.tracking_type,
        ai_algorithm=exercise.ai_algorithm,
        translations=[
            ExerciseTranslationRead(
                language=t.language, name=t.name, overview=t.overview,
                instructions=t.instructions, tips=t.tips,
            )
            for t in translations
        ],
        muscles=[ExerciseMuscleRead(muscle_id=m, role=r, name=n) for m, r, n in muscles],
        equipment=[ExerciseEquipmentRead(equipment_id=i, name=n) for i, n in equipment],
        media=[
            ExerciseMediaRead(
                media_id=m.media_id, media_type=m.media_type, storage_key=m.storage_key,
                sort_order=m.sort_order, url=url_or_none(m.storage_key),
            )
            for m in media
        ],
        usage=_usage(db, exercise_id),
        created_at=exercise.created_at,
        updated_at=exercise.updated_at,
    )


def _check_references(db: Session, payload: ExerciseWrite) -> None:
    muscle_ids = {m.muscle_id for m in payload.muscles}
    if muscle_ids:
        found = set(db.scalars(select(MuscleGroup.muscle_id).where(MuscleGroup.muscle_id.in_(muscle_ids))))
        if muscle_ids - found:
            raise conflict(f"Không có nhóm cơ id {sorted(muscle_ids - found)}")
    equipment_ids = set(payload.equipment_ids)
    if equipment_ids:
        found = set(db.scalars(select(Equipment.equipment_id).where(Equipment.equipment_id.in_(equipment_ids))))
        if equipment_ids - found:
            raise conflict(f"Không có thiết bị id {sorted(equipment_ids - found)}")


def _apply(db: Session, exercise: Exercise, payload: ExerciseWrite) -> None:
    """Write the scalar fields and replace the four child lists.

    Children are diffed rather than deleted and re-inserted, so rows that
    survive keep their ids (media rows especially: nothing else changes).
    """
    exercise.exercise_type = payload.exercise_type
    exercise.mechanics = payload.mechanics
    exercise.force_type = payload.force_type
    exercise.experience_level = payload.experience_level
    exercise.tracking_type = payload.tracking_type
    exercise.updated_at = func.now()
    eid = exercise.exercise_id

    existing_t = {
        t.language: t
        for t in db.scalars(select(ExerciseTranslation).where(ExerciseTranslation.exercise_id == eid))
    }
    wanted = {t.language for t in payload.translations}
    for language, row in existing_t.items():
        if language not in wanted:
            db.delete(row)
    for item in payload.translations:
        row = existing_t.get(item.language)
        if row is None:
            row = ExerciseTranslation(exercise_id=eid, language=item.language, name=item.name)
            db.add(row)
        row.name = item.name
        row.overview = item.overview
        row.instructions = item.instructions
        row.tips = item.tips
        row.updated_at = func.now()

    existing_m = {
        m.muscle_id: m
        for m in db.scalars(select(ExerciseMuscle).where(ExerciseMuscle.exercise_id == eid))
    }
    wanted_m = {m.muscle_id: m.role for m in payload.muscles}
    for muscle_id, row in existing_m.items():
        if muscle_id not in wanted_m:
            db.delete(row)
    for muscle_id, role in wanted_m.items():
        row = existing_m.get(muscle_id)
        if row is None:
            db.add(ExerciseMuscle(exercise_id=eid, muscle_id=muscle_id, role=role))
        else:
            row.role = role

    existing_e = set(db.scalars(
        select(ExerciseEquipment.equipment_id).where(ExerciseEquipment.exercise_id == eid)
    ))
    wanted_e = set(payload.equipment_ids)
    if existing_e - wanted_e:
        db.execute(delete(ExerciseEquipment).where(
            ExerciseEquipment.exercise_id == eid,
            ExerciseEquipment.equipment_id.in_(existing_e - wanted_e),
        ))
    for equipment_id in wanted_e - existing_e:
        db.add(ExerciseEquipment(exercise_id=eid, equipment_id=equipment_id))

    existing_media = {
        (m.media_type, m.storage_key): m
        for m in db.scalars(select(ExerciseMedia).where(ExerciseMedia.exercise_id == eid))
    }
    wanted_media = {(m.media_type, m.storage_key): m for m in payload.media}
    for key, row in existing_media.items():
        if key not in wanted_media:
            db.delete(row)
    for key, item in wanted_media.items():
        row = existing_media.get(key)
        if row is None:
            db.add(ExerciseMedia(
                exercise_id=eid, media_type=item.media_type,
                storage_key=item.storage_key, sort_order=item.sort_order,
            ))
        else:
            row.sort_order = item.sort_order


@router.get("/{exercise_id}", response_model=ExerciseAdminDetail)
def get_exercise(exercise_id: int, _: ExercisesView, db: DbSession) -> ExerciseAdminDetail:
    return _detail(db, exercise_id)


@router.post("", response_model=ExerciseAdminDetail, status_code=status.HTTP_201_CREATED)
def create_exercise(payload: ExerciseWrite, _: ExercisesCreate, db: DbSession) -> ExerciseAdminDetail:
    _check_references(db, payload)
    with committing(db):
        exercise = Exercise(tracking_type=payload.tracking_type)
        db.add(exercise)
        db.flush()
        _apply(db, exercise, payload)
    return _detail(db, exercise.exercise_id)


@router.put("/{exercise_id}", response_model=ExerciseAdminDetail)
def update_exercise(
    exercise_id: int, payload: ExerciseWrite, _: ExercisesUpdate, db: DbSession
) -> ExerciseAdminDetail:
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise not_found("bài tập")
    _check_references(db, payload)
    with committing(db):
        _apply(db, exercise, payload)
    return _detail(db, exercise_id)


@router.delete("/{exercise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exercise(exercise_id: int, _: ExercisesDelete, db: DbSession) -> Response:
    """Refused while anything points at it.

    Plans and sessions would block the delete anyway (RESTRICT), but personal
    records and achievements CASCADE: deleting would quietly erase users'
    records. Checking all four first gives one sentence naming the reason.
    """
    if db.get(Exercise, exercise_id) is None:
        raise not_found("bài tập")
    usage = _usage(db, exercise_id)
    reasons = []
    if usage.plan_exercises:
        reasons.append(f"{usage.plan_exercises} mục trong kế hoạch tập")
    if usage.session_exercises:
        reasons.append(f"{usage.session_exercises} buổi tập đã ghi")
    if usage.personal_records:
        reasons.append(f"{usage.personal_records} kỷ lục cá nhân")
    if usage.achievements:
        reasons.append(f"{usage.achievements} thành tích")
    if reasons:
        raise conflict("Không xoá được: bài tập đang được dùng trong " + ", ".join(reasons))
    with committing(db):
        db.execute(delete(Exercise).where(Exercise.exercise_id == exercise_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)

