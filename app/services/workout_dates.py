"""Editing a plan one calendar date at a time.

The planning tables are shaped around reusable days: a `workout_day` holds an
exercise list, and a `schedules` row says when it happens. The calendar screen
works the other way round - the user picks 15/09 and edits what is on it, and
has no concept of a day that exists apart from its date.

This module is the join between the two. One date maps to one `workout_day`
reached through a `schedules` row with that `scheduled_date`, created on first
use and removed once the date has no exercises left. Doing it here rather than
in the client keeps day, schedule and exercises in a single transaction: a
client sequence of "create day, create schedule, add exercises" can fail
halfway and strand a nameless empty day in the plan.

Recurring rules (`schedules.day_of_week`) are deliberately not touched. Editing
a date must affect that date only.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schedules import Schedule
from app.models.workout_days import WorkoutDay
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan
from app.services.workout_ordering import lock_parent, next_order


def find_day_for_date(
    db: Session, plan_id: int, on_date: date
) -> WorkoutDay | None:
    """The plan's day sitting on this exact date, if there is one.

    Matches only `scheduled_date`; a weekly rule that happens to fall on this
    date is a different thing and is not editable per-date.
    """
    return db.scalar(
        select(WorkoutDay)
        .join(Schedule, Schedule.workout_day_id == WorkoutDay.workout_day_id)
        .where(
            WorkoutDay.plan_id == plan_id,
            Schedule.scheduled_date == on_date,
        )
    )


def replace_date_exercises(
    db: Session,
    plan: WorkoutPlan,
    user_id: uuid.UUID,
    on_date: date,
    exercise_ids: list[int],
) -> WorkoutDay | None:
    """Make this date's exercise list exactly `exercise_ids`, in that order.

    Returns the day, or None when the date ends up empty.

    Exercises already on the date are kept as the same rows rather than
    deleted and recreated, so their target sets survive a trip through the
    picker. Only what the user actually unticked goes away - re-ticking an
    exercise you never removed must not silently wipe its sets.
    """
    # Duplicates would collide on the order column, and the picker cannot
    # express "this exercise twice" anyway. First mention wins.
    wanted: list[int] = list(dict.fromkeys(exercise_ids))

    day = find_day_for_date(db, plan.plan_id, on_date)

    if not wanted:
        if day is not None:
            # Removing the last exercise removes the date: an empty day would
            # show up on the calendar as a workout with nothing in it.
            # CASCADE takes its schedule row too.
            db.delete(day)
        return None

    if day is None:
        lock_parent(db, WorkoutPlan.plan_id, plan.plan_id)
        day = WorkoutDay(
            plan_id=plan.plan_id,
            # The screen formats the date itself, so this is a stable
            # identifier rather than a label anybody reads.
            name=on_date.isoformat(),
            day_order=next_order(
                db, WorkoutDay.day_order, WorkoutDay.plan_id == plan.plan_id
            ),
        )
        db.add(day)
        db.flush()
        db.add(
            Schedule(
                user_id=user_id,
                workout_day_id=day.workout_day_id,
                scheduled_date=on_date,
            )
        )

    existing = {
        we.exercise_id: we
        for we in db.scalars(
            select(WorkoutExercise).where(
                WorkoutExercise.workout_day_id == day.workout_day_id
            )
        ).all()
    }

    for exercise_id, workout_exercise in existing.items():
        if exercise_id not in wanted:
            db.delete(workout_exercise)

    # Positions are rewritten to match the order the picker returned. Every
    # row moves inside this one transaction, which the DEFERRABLE unique
    # constraint on (workout_day_id, exercise_order) allows.
    for position, exercise_id in enumerate(wanted, start=1):
        workout_exercise = existing.get(exercise_id)
        if workout_exercise is None:
            db.add(
                WorkoutExercise(
                    workout_day_id=day.workout_day_id,
                    exercise_id=exercise_id,
                    exercise_order=position,
                )
            )
        else:
            workout_exercise.exercise_order = position

    db.flush()
    return day
