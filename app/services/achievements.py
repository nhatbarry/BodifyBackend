"""Evaluating achievements against the training record.

Every requirement is answered by a query over what actually happened, never by
a counter the application increments. That is what makes the whole thing safe
to run on every finish: reopening a session and finishing it again re-counts
the same completed sessions and arrives at the same number, where `count += 1`
would have quietly awarded "10 workouts" to someone who has done nine.

The insert is `ON CONFLICT DO NOTHING` against `uq_user_achievements`, so
re-evaluation cannot unlock the same badge twice either.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import Numeric, and_, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.progress import (
    EXERCISE_DURATION,
    PERSONAL_RECORD,
    PLAN_COMPLETION,
    TOTAL_VOLUME,
    WORKOUT_COUNT,
    WORKOUT_STREAK,
    Achievement,
    PersonalBest,
    UserAchievement,
)
from app.models.schedules import Schedule
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_days import WorkoutDay
from app.models.workout_plans import WorkoutPlan
from app.models.workout_sessions import COMPLETED, WorkoutSession


def completed_workout_count(db: Session, user_id: uuid.UUID) -> int:
    """Sessions the user actually finished.

    Counted, not tracked. A session that is reopened and finished again is
    still one session.
    """
    return (
        db.execute(
            select(func.count())
            .select_from(WorkoutSession)
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == COMPLETED,
            )
        ).scalar_one()
        or 0
    )


def total_volume_kg(db: Session, user_id: uuid.UUID) -> float:
    """Sum of reps x weight over every completed set the user has logged.

    Summed per set and then added up, never `sum(reps) x sum(weight)` - the
    second is a different and meaningless number.
    """
    return float(
        db.execute(
            select(func.coalesce(func.sum(SessionSet.reps * SessionSet.weight_kg), 0))
            .select_from(SessionSet)
            .join(
                SessionExercise,
                SessionExercise.session_exercise_id
                == SessionSet.session_exercise_id,
            )
            .join(
                WorkoutSession,
                WorkoutSession.session_id == SessionExercise.session_id,
            )
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == COMPLETED,
                SessionSet.completed.is_(True),
            )
        ).scalar_one()
        or 0
    )


def workout_dates(db: Session, user_id: uuid.UUID) -> list[date]:
    """Distinct local dates on which the user finished a workout, ascending."""
    rows = db.execute(
        select(func.date(WorkoutSession.completed_at))
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == COMPLETED,
            WorkoutSession.completed_at.is_not(None),
        )
        .distinct()
        .order_by(func.date(WorkoutSession.completed_at))
    ).scalars().all()
    return [r for r in rows if r is not None]


def longest_streak(days: list[date]) -> int:
    """Longest run of consecutive days in an ascending, de-duplicated list.

    Rest days are simply days with no workout: they end a run rather than
    extending it. That is the plain reading of "7 days in a row", and it is why
    plan adherence - which would have to know a rest day was *scheduled* - is a
    different measure that Phase 4 deliberately does not attempt.

    The longest run is used rather than the current one, so a badge already
    earned is never taken away by a quiet week.
    """
    if not days:
        return 0

    longest = 1
    run = 1
    for previous, current in zip(days, days[1:]):
        if current - previous == timedelta(days=1):
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return longest


def best_duration_for_exercise(
    db: Session, user_id: uuid.UUID, exercise_id: int
) -> int:
    """Longest hold the user has completed for one exercise, in seconds."""
    return int(
        db.execute(
            select(func.coalesce(func.max(SessionSet.duration_seconds), 0))
            .select_from(SessionSet)
            .join(
                SessionExercise,
                SessionExercise.session_exercise_id
                == SessionSet.session_exercise_id,
            )
            .join(
                WorkoutSession,
                WorkoutSession.session_id == SessionExercise.session_id,
            )
            .where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.status == COMPLETED,
                SessionExercise.exercise_id == exercise_id,
                SessionSet.completed.is_(True),
            )
        ).scalar_one()
        or 0
    )


def personal_record_count(db: Session, user_id: uuid.UUID) -> int:
    """How many distinct records the user currently holds."""
    return (
        db.execute(
            select(func.count())
            .select_from(PersonalBest)
            .where(PersonalBest.user_id == user_id)
        ).scalar_one()
        or 0
    )


def completed_plan_count(db: Session, user_id: uuid.UUID) -> int:
    """Plans whose every scheduled training date was actually trained.

    A plan only has a window once it has both a start date and a length, so an
    open-ended plan can never be "completed" - there is no point at which it
    was supposed to end.

    Rest days are excluded from the denominator: a plan that schedules a rest
    day is not asking the user to train, and counting it would make every plan
    impossible to finish.
    """
    plans = db.execute(
        select(WorkoutPlan.plan_id, WorkoutPlan.start_date, WorkoutPlan.duration_weeks)
        .where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.start_date.is_not(None),
            WorkoutPlan.duration_weeks.is_not(None),
        )
    ).all()

    trained = set(workout_dates(db, user_id))
    completed = 0

    for plan_id, start_date, duration_weeks in plans:
        end_date = start_date + timedelta(weeks=duration_weeks)

        scheduled = db.execute(
            select(Schedule.scheduled_date)
            .join(WorkoutDay, WorkoutDay.workout_day_id == Schedule.workout_day_id)
            .where(
                WorkoutDay.plan_id == plan_id,
                WorkoutDay.is_rest_day.is_(False),
                Schedule.scheduled_date.is_not(None),
                Schedule.scheduled_date >= start_date,
                Schedule.scheduled_date < end_date,
            )
            .distinct()
        ).scalars().all()

        if scheduled and all(day in trained for day in scheduled):
            completed += 1

    return completed


def evaluate(db: Session, user_id: uuid.UUID) -> list[Achievement]:
    """Unlock whatever the user now qualifies for. Returns the new unlocks.

    Reads the whole catalog and re-checks every requirement rather than only
    the ones this session could plausibly have moved. There are nine rows; the
    cost of being exhaustive is a few counts, and the cost of being clever is
    an achievement that never fires because nobody thought of the path to it.

    Does not commit - the caller owns the transaction.
    """
    already_unlocked = set(
        db.execute(
            select(UserAchievement.achievement_id).where(
                UserAchievement.user_id == user_id
            )
        ).scalars().all()
    )

    catalog = db.execute(
        select(Achievement).where(Achievement.is_active.is_(True))
    ).scalars().all()

    pending = [a for a in catalog if a.achievement_id not in already_unlocked]
    if not pending:
        return []

    # Computed once and shared, because several achievements ask the same
    # question with different thresholds.
    measured: dict[str, float] = {}

    def measure(achievement: Achievement) -> float:
        kind = achievement.achievement_type

        if kind == EXERCISE_DURATION:
            # Keyed per exercise: two plank tiers ask about the same movement,
            # but a future badge for another hold must not reuse the answer.
            key = f"{kind}:{achievement.exercise_id}"
            if key not in measured:
                measured[key] = float(
                    best_duration_for_exercise(db, user_id, achievement.exercise_id)
                    if achievement.exercise_id is not None
                    else 0
                )
            return measured[key]

        if kind not in measured:
            if kind == WORKOUT_COUNT:
                measured[kind] = float(completed_workout_count(db, user_id))
            elif kind == WORKOUT_STREAK:
                measured[kind] = float(longest_streak(workout_dates(db, user_id)))
            elif kind == TOTAL_VOLUME:
                measured[kind] = total_volume_kg(db, user_id)
            elif kind == PERSONAL_RECORD:
                measured[kind] = float(personal_record_count(db, user_id))
            elif kind == PLAN_COMPLETION:
                measured[kind] = float(completed_plan_count(db, user_id))
            else:
                measured[kind] = 0.0
        return measured[kind]

    unlocked: list[Achievement] = []

    for achievement in pending:
        value = measure(achievement)
        if value < float(achievement.requirement_value):
            continue

        # DO NOTHING rather than a pre-check: two finishes racing each other
        # would both read an empty `already_unlocked`, and only the constraint
        # can settle it.
        inserted = db.execute(
            pg_insert(UserAchievement)
            .values(
                user_id=user_id,
                achievement_id=achievement.achievement_id,
                progress_value=value,
            )
            .on_conflict_do_nothing(constraint="uq_user_achievements")
            .returning(UserAchievement.user_achievement_id)
        ).scalar_one_or_none()

        if inserted is not None:
            unlocked.append(achievement)

    return unlocked
