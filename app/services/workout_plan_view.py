"""Assembling plan responses.

Every function here loads a whole level at a time. A plan with six days of
eight exercises would otherwise cost one query per exercise for its name and
another for its thumbnail; batching keeps the nested response at four queries
no matter how large the plan is.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.workout_days import WorkoutDay
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.exercises import Exercise
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan
from app.schemas.workout_plans import (
    WorkoutDayDetail,
    WorkoutDaySummary,
    WorkoutExerciseRead,
    WorkoutPlanDetail,
    WorkoutPlanListItem,
    WorkoutSetRead,
)
from app.services.exercise_media import list_image_urls
from app.services.exercise_translation import best_translation


def build_exercise_reads(
    db: Session, workout_exercises: Sequence[WorkoutExercise], language: str
) -> list[WorkoutExerciseRead]:
    """Attach names, thumbnails and target sets to a batch of planned exercises."""
    if not workout_exercises:
        return []

    workout_exercise_ids = [we.workout_exercise_id for we in workout_exercises]
    exercise_ids = [we.exercise_id for we in workout_exercises]

    translation = best_translation(language)
    names = {
        exercise_id: (name, translation_language)
        for exercise_id, name, translation_language in db.execute(
            select(translation.c.exercise_id, translation.c.name, translation.c.language)
            .where(translation.c.exercise_id.in_(set(exercise_ids)))
        ).all()
    }

    images = list_image_urls(db, exercise_ids)
    tracking = dict(
        db.execute(
            select(Exercise.exercise_id, Exercise.tracking_type).where(
                Exercise.exercise_id.in_(set(exercise_ids))
            )
        ).all()
    )

    sets_by_exercise: dict[int, list[WorkoutSetRead]] = defaultdict(list)
    for row in db.scalars(
        select(WorkoutExerciseSet)
        .where(WorkoutExerciseSet.workout_exercise_id.in_(workout_exercise_ids))
        .order_by(
            WorkoutExerciseSet.workout_exercise_id, WorkoutExerciseSet.set_order
        )
    ).all():
        sets_by_exercise[row.workout_exercise_id].append(
            WorkoutSetRead.model_validate(row)
        )

    result = []
    for we in workout_exercises:
        name, translation_language = names.get(we.exercise_id, (None, None))
        result.append(
            WorkoutExerciseRead(
                workout_exercise_id=we.workout_exercise_id,
                exercise_id=we.exercise_id,
                exercise_order=we.exercise_order,
                notes=we.notes,
                name=name,
                language=translation_language,
                tracking_type=tracking.get(we.exercise_id, "reps"),
                icon_url=images.get(we.exercise_id, (None, None))[0],
                thumbnail_url=images.get(we.exercise_id, (None, None))[1],
                sets=sets_by_exercise.get(we.workout_exercise_id, []),
            )
        )
    return result


def build_plan_detail(
    db: Session, plan: WorkoutPlan, language: str
) -> WorkoutPlanDetail:
    """The full tree: plan -> days -> exercises -> target sets."""
    days = db.scalars(
        select(WorkoutDay)
        .where(WorkoutDay.plan_id == plan.plan_id)
        .order_by(WorkoutDay.day_order)
    ).all()

    day_ids = [day.workout_day_id for day in days]
    workout_exercises = (
        db.scalars(
            select(WorkoutExercise)
            .where(WorkoutExercise.workout_day_id.in_(day_ids))
            .order_by(WorkoutExercise.workout_day_id, WorkoutExercise.exercise_order)
        ).all()
        if day_ids
        else []
    )

    reads = build_exercise_reads(db, workout_exercises, language)
    by_day: dict[int, list[WorkoutExerciseRead]] = defaultdict(list)
    for we, read in zip(workout_exercises, reads):
        by_day[we.workout_day_id].append(read)

    return WorkoutPlanDetail(
        plan_id=plan.plan_id,
        name=plan.name,
        description=plan.description,
        goal=plan.goal,
        duration_weeks=plan.duration_weeks,
        status=plan.status,
        is_active=plan.is_active,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
        days=[
            WorkoutDayDetail(
                workout_day_id=day.workout_day_id,
                name=day.name,
                day_order=day.day_order,
                is_rest_day=day.is_rest_day,
                description=day.description,
                exercises=by_day.get(day.workout_day_id, []),
            )
            for day in days
        ],
    )


def build_plan_list(
    db: Session, plans: Sequence[WorkoutPlan]
) -> list[WorkoutPlanListItem]:
    """Plans with a shallow day preview - names and counts, no exercises.

    Day names are text the user typed, not catalog translations, so this
    response needs no language parameter.
    """
    if not plans:
        return []

    plan_ids = [plan.plan_id for plan in plans]
    exercise_count = (
        func.count(WorkoutExercise.workout_exercise_id).label("exercise_count")
    )
    rows = db.execute(
        select(
            WorkoutDay.plan_id,
            WorkoutDay.workout_day_id,
            WorkoutDay.name,
            WorkoutDay.day_order,
            WorkoutDay.is_rest_day,
            exercise_count,
        )
        .join(
            WorkoutExercise,
            WorkoutExercise.workout_day_id == WorkoutDay.workout_day_id,
            isouter=True,
        )
        .where(WorkoutDay.plan_id.in_(plan_ids))
        .group_by(WorkoutDay.workout_day_id)
        .order_by(WorkoutDay.plan_id, WorkoutDay.day_order)
    ).all()

    days_by_plan: dict[int, list[WorkoutDaySummary]] = defaultdict(list)
    for plan_id, day_id, name, day_order, is_rest_day, count in rows:
        days_by_plan[plan_id].append(
            WorkoutDaySummary(
                workout_day_id=day_id,
                name=name,
                day_order=day_order,
                is_rest_day=is_rest_day,
                exercise_count=count,
            )
        )

    return [
        WorkoutPlanListItem(
            plan_id=plan.plan_id,
            name=plan.name,
            description=plan.description,
            goal=plan.goal,
            duration_weeks=plan.duration_weeks,
            status=plan.status,
            is_active=plan.is_active,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
            days=days_by_plan.get(plan.plan_id, []),
        )
        for plan in plans
    ]
