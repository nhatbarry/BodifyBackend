"""Phase 4 read endpoints: progress, personal records, achievements.

All read-only. The writes happen in one place - `POST
/workout-sessions/{id}/finish` - because a record and an unlock are
consequences of finishing a workout, not things a client gets to assert.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.exercises import Exercise
from app.models.progress import (
    EXERCISE_DURATION,
    PERSONAL_RECORD,
    PLAN_COMPLETION,
    TOTAL_VOLUME,
    WORKOUT_COUNT,
    WORKOUT_STREAK,
    Achievement,
    PersonalBest,
    PersonalRecord,
    UserAchievement,
)
from app.schemas.progress import (
    AchievementRead,
    ActivityDayRead,
    ExerciseProgressionRead,
    PersonalBestRead,
    PersonalRecordRead,
    ProgressPointRead,
    ProgressSummaryRead,
    RecentExerciseRead,
)
from app.services import achievements as achievement_service
from app.services import progress as progress_service
from app.services.exercise_translation import DEFAULT_LANG, best_translation
from app.services.exercise_media import list_image_urls

router = APIRouter(tags=["progress"])


def _validate_range(from_date: date | None, to_date: date | None) -> None:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from must not be after to",
        )


def _names(db, exercise_ids: list[int], language: str) -> dict[int, str]:
    """Catalog names for a set of exercises, in the requested language.

    Reused from the exercise catalog rather than read off the session snapshot:
    a progress chart is about the exercise as it is named today, not as it was
    named the day it was performed. The history keeps its own snapshot for the
    opposite reason.
    """
    if not exercise_ids:
        return {}
    translation = best_translation(language)
    return {
        exercise_id: name
        for exercise_id, name in db.execute(
            select(translation.c.exercise_id, translation.c.name).where(
                translation.c.exercise_id.in_(set(exercise_ids))
            )
        ).all()
        if name
    }


@router.get("/progress/summary", response_model=ProgressSummaryRead)
def get_progress_summary(
    current_user: CurrentUserDep,
    db: DbSession,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> ProgressSummaryRead:
    """Completion, performance, volume and frequency over a period.

    With no dates this covers everything the user has ever done, and the
    frequency is measured across the span between their first and last workout
    rather than against the calendar - two workouts in the one week somebody
    has been training is two a week, not two since January.
    """
    _validate_range(from_date, to_date)
    result = progress_service.summary(db, current_user.id, from_date, to_date)
    return ProgressSummaryRead(**result.__dict__)


@router.get("/progress/activity", response_model=list[ActivityDayRead])
def get_recent_activity(
    current_user: CurrentUserDep,
    db: DbSession,
    # Up to a year: the home screen's grid sizes itself to the card and
    # asks for as many weeks as it can draw, which is more than a quarter
    # once the squares are GitHub-sized.
    days: int = Query(default=7, ge=1, le=366),
) -> list[ActivityDayRead]:
    """Training done on each of the last `days` days, oldest first.

    Untrained days come back too, with zeroes. A rest day is part of the shape
    of a week, and a strip with holes punched in it cannot be read.
    """
    return [
        ActivityDayRead(**day.__dict__)
        for day in progress_service.recent_activity(db, current_user.id, days)
    ]


@router.get("/progress/recent-exercises", response_model=list[RecentExerciseRead])
def get_recent_exercises(
    current_user: CurrentUserDep,
    db: DbSession,
    limit: int = Query(default=4, ge=1, le=20),
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> list[RecentExerciseRead]:
    """The exercises trained most recently, newest first.

    Names come from the catalog rather than the session snapshot: this is a
    "what have I been doing lately" list about exercises as they are called
    today, not a history entry that has to keep reading the way it read then.
    """
    rows = progress_service.recent_exercises(db, current_user.id, limit)
    if not rows:
        return []

    exercise_ids = [row.exercise_id for row in rows]
    names = _names(db, exercise_ids, language)
    images = list_image_urls(db, exercise_ids)

    return [
        RecentExerciseRead(
            exercise_id=row.exercise_id,
            name=names.get(row.exercise_id),
            tracking_type=row.tracking_type,
            # Icon first, thumbnail as the fallback - the same rule the lists use.
            thumbnail_url=(
                images.get(row.exercise_id, (None, None))[0]
                or images.get(row.exercise_id, (None, None))[1]
            ),
            day=row.day,
            session_id=row.session_id,
            set_count=row.set_count,
            total_reps=row.total_reps,
            total_volume_kg=row.total_volume_kg,
            best_duration_seconds=row.best_duration_seconds,
            best_distance_m=row.best_distance_m,
        )
        for row in rows
    ]


@router.get(
    "/progress/exercises/{exercise_id}", response_model=ExerciseProgressionRead
)
def get_exercise_progression(
    exercise_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> ExerciseProgressionRead:
    """One exercise over time - the best set of each training day.

    `tracking_type` comes back with the series so the client knows which line
    to plot: kilograms for a Bench Press, seconds for a Plank, metres for a
    carry. Plotting all four would give three flat lines and one real one.
    """
    _validate_range(from_date, to_date)

    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found"
        )

    points = progress_service.exercise_progression(
        db, current_user.id, exercise_id, from_date, to_date
    )

    return ExerciseProgressionRead(
        exercise_id=exercise_id,
        name=_names(db, [exercise_id], language).get(exercise_id),
        tracking_type=exercise.tracking_type,
        points=[ProgressPointRead(**point.__dict__) for point in points],
    )


@router.get("/personal-records", response_model=list[PersonalBestRead])
def list_personal_bests(
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> list[PersonalBestRead]:
    """Every record the user currently holds.

    Read from `personal_bests`, which is one row per record rather than a
    DISTINCT ON over the whole history.
    """
    bests = db.scalars(
        select(PersonalBest)
        .where(PersonalBest.user_id == current_user.id)
        .order_by(PersonalBest.exercise_id, PersonalBest.record_type)
    ).all()

    names = _names(db, [best.exercise_id for best in bests], language)

    return [
        PersonalBestRead(
            exercise_id=best.exercise_id,
            name=names.get(best.exercise_id),
            record_type=best.record_type,
            value=best.value,
            unit=best.unit,
            achieved_at=best.achieved_at,
        )
        for best in bests
    ]


@router.get(
    "/personal-records/{exercise_id}", response_model=list[PersonalRecordRead]
)
def get_personal_record_history(
    exercise_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
    record_type: str | None = Query(default=None, max_length=30),
) -> list[PersonalRecordRead]:
    """Every time a record was broken for one exercise, oldest first.

    This is the series behind "40 -> 45 -> 50 kg". Filter by `record_type` to
    plot one line; without it every kind of record for the exercise comes back
    and the client has to group them.
    """
    query = (
        select(PersonalRecord)
        .where(
            PersonalRecord.user_id == current_user.id,
            PersonalRecord.exercise_id == exercise_id,
        )
        .order_by(PersonalRecord.achieved_at)
    )
    if record_type is not None:
        query = query.where(PersonalRecord.record_type == record_type)

    return [
        PersonalRecordRead.model_validate(record)
        for record in db.scalars(query).all()
    ]


@router.get("/achievements", response_model=list[AchievementRead])
def list_achievements(
    current_user: CurrentUserDep,
    db: DbSession,
) -> list[AchievementRead]:
    """The whole catalog, with what the user has unlocked and how far along
    they are on the rest.

    The progress figures are computed here rather than stored, the same way
    they are when an unlock is decided at finish - so the number on a locked
    badge and the number that eventually unlocks it can never disagree.
    """
    catalog = db.scalars(
        select(Achievement)
        .where(Achievement.is_active.is_(True))
        .order_by(Achievement.achievement_type, Achievement.requirement_value)
    ).all()

    unlocked = {
        row.achievement_id: row
        for row in db.scalars(
            select(UserAchievement).where(
                UserAchievement.user_id == current_user.id
            )
        ).all()
    }

    # Each measure is computed at most once even though several badges share
    # it - three workout-count tiers ask the same question.
    cache: dict[str, float] = {}

    def current_value(achievement: Achievement) -> float:
        kind = achievement.achievement_type
        if kind == EXERCISE_DURATION:
            key = f"{kind}:{achievement.exercise_id}"
            if key not in cache:
                cache[key] = float(
                    achievement_service.best_duration_for_exercise(
                        db, current_user.id, achievement.exercise_id
                    )
                    if achievement.exercise_id is not None
                    else 0
                )
            return cache[key]

        if kind not in cache:
            if kind == WORKOUT_COUNT:
                cache[kind] = float(
                    achievement_service.completed_workout_count(db, current_user.id)
                )
            elif kind == WORKOUT_STREAK:
                cache[kind] = float(
                    achievement_service.longest_streak(
                        achievement_service.workout_dates(db, current_user.id)
                    )
                )
            elif kind == TOTAL_VOLUME:
                cache[kind] = achievement_service.total_volume_kg(
                    db, current_user.id
                )
            elif kind == PERSONAL_RECORD:
                cache[kind] = float(
                    achievement_service.personal_record_count(db, current_user.id)
                )
            elif kind == PLAN_COMPLETION:
                cache[kind] = float(
                    achievement_service.completed_plan_count(db, current_user.id)
                )
            else:
                cache[kind] = 0.0
        return cache[kind]

    result: list[AchievementRead] = []
    for achievement in catalog:
        row = unlocked.get(achievement.achievement_id)
        result.append(
            AchievementRead(
                achievement_id=achievement.achievement_id,
                code=achievement.code,
                name=achievement.name,
                description=achievement.description,
                icon_url=achievement.icon_url,
                achievement_type=achievement.achievement_type,
                requirement_value=achievement.requirement_value,
                requirement_unit=achievement.requirement_unit,
                exercise_id=achievement.exercise_id,
                unlocked=row is not None,
                achieved_at=row.achieved_at if row else None,
                current_value=current_value(achievement),
            )
        )
    return result
