"""Turning the session record into the numbers a progress screen shows.

Everything here is computed on read. There is no `user_stats` table, for the
same reason BMI is not stored: a cached total is a second source of truth, and
the moment a set is corrected the two disagree with no way to tell which is
right.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exercises import Exercise
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_sessions import COMPLETED, WorkoutSession

# How an exercise is measured, mirrored from `exercises.tracking_type`.
REPS = "reps"
DURATION = "duration"
DISTANCE = "distance"
REPS_DURATION = "reps_duration"


@dataclass
class SetRow:
    """One completed set, with whatever it was aiming at."""

    tracking_type: str
    reps: int | None
    weight_kg: Decimal | None
    duration_seconds: int | None
    distance_m: Decimal | None
    target_reps: int | None
    target_weight_kg: Decimal | None
    target_duration_seconds: int | None
    target_distance_m: Decimal | None
    completed: bool

    @property
    def has_target(self) -> bool:
        """Whether the plan asked for anything measurable on this set.

        A set added mid-workout has no target. It is a real performance and
        stays in the history, but it cannot be *completed against a plan*, so
        it is excluded from completion and performance rather than counted as
        a miss.
        """
        return any(
            value is not None
            for value in (
                self.target_reps,
                self.target_weight_kg,
                self.target_duration_seconds,
                self.target_distance_m,
            )
        )


def _ratio(actual, target) -> float | None:
    if actual is None or target is None or float(target) <= 0:
        return None
    return float(actual) / float(target)


def set_performance(row: SetRow) -> float | None:
    """How close one set came to its target, as a fraction.

    Deliberately not one formula for everything. Comparing a Plank's seconds
    against a Bench Press's reps with the same expression produces a number
    that is arithmetically fine and means nothing.

    Not clamped to 1.0: beating the target is information, and a screen that
    shows 110% is telling the truth about a good day.
    """
    if row.tracking_type == DISTANCE:
        return _ratio(row.distance_m, row.target_distance_m)

    if row.tracking_type == DURATION:
        return _ratio(row.duration_seconds, row.target_duration_seconds)

    if row.tracking_type == REPS_DURATION:
        # Both axes are real for these, so neither can stand in for the other.
        # Whichever ones were actually planned are averaged; a set that only
        # planned reps is judged on reps alone.
        parts = [
            part
            for part in (
                _ratio(row.reps, row.target_reps),
                _ratio(row.duration_seconds, row.target_duration_seconds),
            )
            if part is not None
        ]
        return sum(parts) / len(parts) if parts else None

    # Plain reps. When the set is loaded and the plan said how much, volume is
    # the honest comparison: 10 reps at 36 kg against a planned 10 at 40 is not
    # 100% of the work, and reps alone would call it that.
    if (
        row.weight_kg is not None
        and row.target_weight_kg is not None
        and row.reps is not None
        and row.target_reps is not None
    ):
        planned = float(row.target_weight_kg) * row.target_reps
        if planned > 0:
            return (float(row.weight_kg) * row.reps) / planned

    return _ratio(row.reps, row.target_reps)


def _set_rows(
    db: Session,
    user_id: uuid.UUID,
    from_date: date | None,
    to_date: date | None,
    exercise_id: int | None = None,
) -> list[SetRow]:
    query = (
        select(
            Exercise.tracking_type,
            SessionSet.reps,
            SessionSet.weight_kg,
            SessionSet.duration_seconds,
            SessionSet.distance_m,
            SessionSet.target_reps,
            SessionSet.target_weight_kg,
            SessionSet.target_duration_seconds,
            SessionSet.target_distance_m,
            SessionSet.completed,
        )
        .select_from(SessionSet)
        .join(
            SessionExercise,
            SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
        )
        .join(
            WorkoutSession,
            WorkoutSession.session_id == SessionExercise.session_id,
        )
        .join(Exercise, Exercise.exercise_id == SessionExercise.exercise_id)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == COMPLETED,
        )
    )

    if from_date is not None:
        query = query.where(func.date(WorkoutSession.completed_at) >= from_date)
    if to_date is not None:
        query = query.where(func.date(WorkoutSession.completed_at) <= to_date)
    if exercise_id is not None:
        query = query.where(SessionExercise.exercise_id == exercise_id)

    return [SetRow(*row) for row in db.execute(query).all()]


@dataclass
class ProgressSummary:
    planned_sets: int
    completed_planned_sets: int
    completion_percent: float | None
    performance_percent: float | None
    total_volume_kg: float
    session_count: int
    workouts_per_week: float | None
    days_span: int | None


def summary(
    db: Session,
    user_id: uuid.UUID,
    from_date: date | None = None,
    to_date: date | None = None,
) -> ProgressSummary:
    """The headline figures for a period.

    Completion and performance both ignore sets with no target - see
    `SetRow.has_target`. They answer different questions about the same sets:
    completion is "did you do it", performance is "how well".
    """
    rows = _set_rows(db, user_id, from_date, to_date)
    planned = [row for row in rows if row.has_target]

    completed_planned = [row for row in planned if row.completed]
    completion = (
        len(completed_planned) / len(planned) * 100 if planned else None
    )

    scores = [
        score
        for score in (set_performance(row) for row in completed_planned)
        if score is not None
    ]
    performance = sum(scores) / len(scores) * 100 if scores else None

    volume = sum(
        float(row.weight_kg) * row.reps
        for row in rows
        if row.completed and row.weight_kg is not None and row.reps is not None
    )

    sessions = db.execute(
        select(
            func.count(),
            func.min(func.date(WorkoutSession.completed_at)),
            func.max(func.date(WorkoutSession.completed_at)),
        ).where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == COMPLETED,
            *(
                [func.date(WorkoutSession.completed_at) >= from_date]
                if from_date
                else []
            ),
            *([func.date(WorkoutSession.completed_at) <= to_date] if to_date else []),
        )
    ).one()

    session_count, first_day, last_day = sessions

    # Frequency counts what was done, not what was scheduled: a plan with three
    # days a week that the user trained twice is two workouts a week.
    span_days: int | None = None
    per_week: float | None = None
    if session_count:
        start = from_date or first_day
        end = to_date or last_day
        if start and end:
            span_days = (end - start).days + 1
            # A rate needs at least a week under it. Two sessions on the only
            # day somebody has trained is arithmetically 14 a week and is not
            # a fact about their training - the floor keeps the first week from
            # reporting a number nobody could sustain.
            per_week = session_count / (max(span_days, 7) / 7)

    return ProgressSummary(
        planned_sets=len(planned),
        completed_planned_sets=len(completed_planned),
        completion_percent=completion,
        performance_percent=performance,
        total_volume_kg=volume,
        session_count=session_count or 0,
        workouts_per_week=per_week,
        days_span=span_days,
    )


@dataclass
class ProgressPoint:
    day: date
    best_weight_kg: float | None
    best_reps: int | None
    best_duration_seconds: int | None
    best_distance_m: float | None
    volume_kg: float


def exercise_progression(
    db: Session,
    user_id: uuid.UUID,
    exercise_id: int,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[ProgressPoint]:
    """One point per training day for a single exercise.

    The best set of the day rather than the average: progression is about what
    the user became capable of, and a heavy top set followed by lighter backoff
    work is progress, not a decline.
    """
    query = (
        select(
            func.date(WorkoutSession.completed_at).label("day"),
            func.max(SessionSet.weight_kg),
            func.max(SessionSet.reps),
            func.max(SessionSet.duration_seconds),
            func.max(SessionSet.distance_m),
            func.coalesce(func.sum(SessionSet.reps * SessionSet.weight_kg), 0),
        )
        .select_from(SessionSet)
        .join(
            SessionExercise,
            SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
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
        .group_by(func.date(WorkoutSession.completed_at))
        .order_by(func.date(WorkoutSession.completed_at))
    )

    if from_date is not None:
        query = query.where(func.date(WorkoutSession.completed_at) >= from_date)
    if to_date is not None:
        query = query.where(func.date(WorkoutSession.completed_at) <= to_date)

    return [
        ProgressPoint(
            day=day,
            best_weight_kg=float(weight) if weight is not None else None,
            best_reps=reps,
            best_duration_seconds=duration,
            best_distance_m=float(distance) if distance is not None else None,
            volume_kg=float(volume or 0),
        )
        for day, weight, reps, duration, distance, volume in db.execute(query).all()
    ]


@dataclass
class ActivityDay:
    """One day of the activity strip."""

    day: date
    completed_sets: int
    volume_kg: float
    session_count: int


def recent_activity(
    db: Session, user_id: uuid.UUID, days: int = 7
) -> list[ActivityDay]:
    """Completed sets per day over the last `days`, oldest first.

    Sets rather than volume, because volume only exists for loaded work: a day
    of planks and carries would read as zero effort, which is the opposite of
    what happened. Sets are the one unit every tracking type produces.

    Every day in the window is returned, trained or not - a rest day is part of
    the shape of a week, and a strip with gaps in it cannot be read.
    """
    end = date.today()
    start = end - timedelta(days=days - 1)

    rows = dict(
        (row[0], row)
        for row in db.execute(
            select(
                func.date(WorkoutSession.completed_at).label("day"),
                func.count(SessionSet.session_set_id),
                func.coalesce(
                    func.sum(SessionSet.reps * SessionSet.weight_kg), 0
                ),
                func.count(func.distinct(WorkoutSession.session_id)),
            )
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
                func.date(WorkoutSession.completed_at) >= start,
                func.date(WorkoutSession.completed_at) <= end,
            )
            .group_by(func.date(WorkoutSession.completed_at))
        ).all()
    )

    result: list[ActivityDay] = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        row = rows.get(day)
        result.append(
            ActivityDay(
                day=day,
                completed_sets=int(row[1]) if row else 0,
                volume_kg=float(row[2] or 0) if row else 0.0,
                session_count=int(row[3]) if row else 0,
            )
        )
    return result


@dataclass
class RecentExercise:
    """One exercise the user trained, with what they did that day."""

    exercise_id: int
    name: str | None
    tracking_type: str
    day: date
    session_id: int
    set_count: int
    total_reps: int
    total_volume_kg: float
    best_duration_seconds: int | None
    best_distance_m: float | None


def recent_exercises(
    db: Session, user_id: uuid.UUID, limit: int = 4
) -> list[RecentExercise]:
    """The exercises trained most recently, newest first, one row each.

    Grouped by (exercise, day) rather than by session: two sessions on one
    afternoon are one training day as far as "what did I do recently" is
    concerned, and an exercise repeated across the week should appear once,
    at its latest date, not four times.

    Aggregated here for the same reason the history totals are: the client
    would otherwise pull every set of every session to add them up.
    """
    day = func.date(WorkoutSession.completed_at).label("day")

    rows = db.execute(
        select(
            SessionExercise.exercise_id,
            Exercise.tracking_type,
            day,
            func.max(WorkoutSession.session_id),
            func.count(SessionSet.session_set_id),
            func.coalesce(func.sum(SessionSet.reps), 0),
            func.coalesce(
                func.sum(SessionSet.reps * SessionSet.weight_kg), 0
            ),
            func.max(SessionSet.duration_seconds),
            func.max(SessionSet.distance_m),
        )
        .select_from(SessionSet)
        .join(
            SessionExercise,
            SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
        )
        .join(
            WorkoutSession,
            WorkoutSession.session_id == SessionExercise.session_id,
        )
        .join(Exercise, Exercise.exercise_id == SessionExercise.exercise_id)
        .where(
            WorkoutSession.user_id == user_id,
            WorkoutSession.status == COMPLETED,
            SessionSet.completed.is_(True),
        )
        .group_by(SessionExercise.exercise_id, Exercise.tracking_type, day)
        .order_by(day.desc())
    ).all()

    # De-duplicated in Python rather than with a window function: the list is
    # short, and "the latest day for each exercise" reads plainly here.
    seen: set[int] = set()
    result: list[RecentExercise] = []
    for (
        exercise_id,
        tracking_type,
        performed_on,
        session_id,
        set_count,
        total_reps,
        volume,
        best_duration,
        best_distance,
    ) in rows:
        if exercise_id in seen:
            continue
        seen.add(exercise_id)
        result.append(
            RecentExercise(
                exercise_id=exercise_id,
                name=None,  # filled in by the endpoint, which knows the language
                tracking_type=tracking_type,
                day=performed_on,
                session_id=session_id,
                set_count=int(set_count or 0),
                total_reps=int(total_reps or 0),
                total_volume_kg=float(volume or 0),
                best_duration_seconds=best_duration,
                best_distance_m=float(best_distance) if best_distance else None,
            )
        )
        if len(result) >= limit:
            break

    return result
