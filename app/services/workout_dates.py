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
from datetime import date, timedelta

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


def create_day_for_date(
    db: Session,
    plan: WorkoutPlan,
    user_id: uuid.UUID,
    on_date: date,
    *,
    is_rest_day: bool = False,
) -> WorkoutDay:
    """Add a day to the plan and pin it to one calendar date.

    Two rows, always together: a `workout_days` row for the content and a
    `schedules` row for the date. A day without its schedule would be
    invisible on the calendar and unreachable by date.

    The name is the ISO date because the screen formats the date itself - it
    is a stable identifier, not a label anybody reads.
    """
    lock_parent(db, WorkoutPlan.plan_id, plan.plan_id)
    day = WorkoutDay(
        plan_id=plan.plan_id,
        name=on_date.isoformat(),
        day_order=next_order(
            db, WorkoutDay.day_order, WorkoutDay.plan_id == plan.plan_id
        ),
        is_rest_day=is_rest_day,
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
    return day


# Three is a rest week's worth of consecutive days off; a fourth is somebody
# drifting out of the habit rather than recovering, and a plan that lets it
# through is not a plan any more.
MAX_CONSECUTIVE_REST_DAYS = 3


class TooManyRestDays(ValueError):
    """Marking this date would make a run of rest days too long."""

    def __init__(self, run_length: int) -> None:
        super().__init__(f"{run_length} consecutive rest days")
        self.run_length = run_length


def _rest_run_length(
    db: Session, plan_id: int, on_date: date
) -> int:
    """How long a run of rest days marking `on_date` would sit in.

    Counts outwards in both directions, because a rest day can be ticked in
    the middle of a gap as easily as at the end of one - three off, then a
    fourth inserted between two of them, is still four in a row.

    A date with no row at all breaks the run: an unplanned day is not a rest
    day, which is the whole reason rest is stored as a row.
    """
    window_start = on_date - timedelta(days=MAX_CONSECUTIVE_REST_DAYS)
    window_end = on_date + timedelta(days=MAX_CONSECUTIVE_REST_DAYS)

    rest_dates = {
        scheduled
        for (scheduled,) in db.execute(
            select(Schedule.scheduled_date)
            .join(WorkoutDay, WorkoutDay.workout_day_id == Schedule.workout_day_id)
            .where(
                WorkoutDay.plan_id == plan_id,
                WorkoutDay.is_rest_day.is_(True),
                Schedule.scheduled_date.is_not(None),
                Schedule.scheduled_date >= window_start,
                Schedule.scheduled_date <= window_end,
            )
        ).all()
    }
    rest_dates.add(on_date)

    run = 1
    cursor = on_date - timedelta(days=1)
    while cursor in rest_dates:
        run += 1
        cursor -= timedelta(days=1)
    cursor = on_date + timedelta(days=1)
    while cursor in rest_dates:
        run += 1
        cursor += timedelta(days=1)
    return run


def set_date_rest_day(
    db: Session,
    plan: WorkoutPlan,
    user_id: uuid.UUID,
    on_date: date,
    is_rest_day: bool,
) -> WorkoutDay | None:
    """Mark a calendar date as a planned rest, or stop marking it.

    A rest day is a row, not an absence: the calendar has to tell "resting
    today" from "nothing scheduled yet", and only a row can carry that.

    Un-ticking leaves a day with no exercises and no meaning, so the day is
    removed - the same rule that removes a date when its last exercise goes.
    """
    day = find_day_for_date(db, plan.plan_id, on_date)

    if is_rest_day and not (day is not None and day.is_rest_day):
        # Checked before anything is written, and skipped when the date is
        # already a rest day - re-ticking what is already ticked must not fail.
        run = _rest_run_length(db, plan.plan_id, on_date)
        if run > MAX_CONSECUTIVE_REST_DAYS:
            raise TooManyRestDays(run)

    if is_rest_day:
        if day is None:
            return create_day_for_date(db, plan, user_id, on_date, is_rest_day=True)
        if day.exercises:
            # Flipping the flag would strand exercises on a day meant to have
            # none. Make the caller clear them rather than deleting their work.
            raise ValueError("day has exercises")
        day.is_rest_day = True
        return day

    if day is None:
        return None
    if day.exercises:
        day.is_rest_day = False
        return day
    db.delete(day)
    return None


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
        day = create_day_for_date(db, plan, user_id, on_date)

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
