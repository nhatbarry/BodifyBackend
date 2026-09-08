"""Assembling session responses.

Everything here loads a whole level at a time. A history screen shows twenty
sessions at once; resolving each one's totals with its own query would be
twenty round trips to render a list.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exercises import Exercise
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_sessions import WorkoutSession
from app.schemas.workout_sessions import (
    SessionExerciseRead,
    SessionSetRead,
    SessionSummary,
    WorkoutSessionDetail,
    WorkoutSessionListItem,
)
from app.services.exercise_media import list_image_urls

EMPTY_SUMMARY = SessionSummary(
    exercise_count=0,
    set_count=0,
    completed_set_count=0,
    total_reps=0,
    total_volume_kg=Decimal("0"),
)


def _set_read(row: SessionSet) -> SessionSetRead:
    result = SessionSetRead.model_validate(row)
    result.volume_kg = row.volume_kg
    result.ai_score_percent = row.ai_score_percent
    return result


def summaries(
    db: Session, session_ids: Sequence[int]
) -> dict[int, SessionSummary]:
    """`{session_id: SessionSummary}` for a batch of sessions, in one query.

    Volume is `sum(reps * weight)` rather than `sum(reps) * sum(weight)`, which
    is the difference between a real number and a meaningless one.
    """
    if not session_ids:
        return {}

    volume = func.coalesce(
        func.sum(SessionSet.reps * SessionSet.weight_kg), 0
    ).label("volume")

    rows = db.execute(
        select(
            SessionExercise.session_id,
            func.count(func.distinct(SessionExercise.session_exercise_id)).label(
                "exercise_count"
            ),
            func.count(SessionSet.session_set_id).label("set_count"),
            func.count(SessionSet.session_set_id)
            .filter(SessionSet.completed.is_(True))
            .label("completed_set_count"),
            func.coalesce(func.sum(SessionSet.reps), 0).label("total_reps"),
            volume,
        )
        .join(
            SessionSet,
            SessionSet.session_exercise_id == SessionExercise.session_exercise_id,
            isouter=True,
        )
        .where(SessionExercise.session_id.in_(set(session_ids)))
        .group_by(SessionExercise.session_id)
    ).all()

    return {
        session_id: SessionSummary(
            exercise_count=exercise_count,
            set_count=set_count,
            completed_set_count=completed_set_count,
            total_reps=int(total_reps or 0),
            total_volume_kg=Decimal(volume_kg or 0),
        )
        for (
            session_id,
            exercise_count,
            set_count,
            completed_set_count,
            total_reps,
            volume_kg,
        ) in rows
    }


def build_list_items(
    db: Session, sessions: Sequence[WorkoutSession]
) -> list[WorkoutSessionListItem]:
    """Sessions with their totals, but without their exercises.

    A history list shows "Push - 45 min - 12 sets", not every set; pulling the
    sets would load the whole training log to draw a screen of summaries.
    """
    if not sessions:
        return []

    by_session = summaries(db, [s.session_id for s in sessions])

    return [
        WorkoutSessionListItem(
            session_id=s.session_id,
            workout_day_id=s.workout_day_id,
            plan_name=s.plan_name,
            day_name=s.day_name,
            scheduled_date=s.scheduled_date,
            started_at=s.started_at,
            completed_at=s.completed_at,
            status=s.status,
            duration_seconds=s.duration_seconds,
            paused_seconds=s.paused_seconds,
            notes=s.notes,
            summary=by_session.get(s.session_id, EMPTY_SUMMARY),
        )
        for s in sessions
    ]


def build_exercise_reads(
    db: Session, session_exercises: Sequence[SessionExercise]
) -> list[SessionExerciseRead]:
    """Recorded exercises with their sets and a picture.

    The name comes from the row's own snapshot, never from a fresh translation
    lookup - that is the whole point of storing it. Only the thumbnail is
    resolved live, because a URL is not history.
    """
    if not session_exercises:
        return []

    ids = [se.session_exercise_id for se in session_exercises]
    exercise_ids = [se.exercise_id for se in session_exercises]
    images = list_image_urls(db, exercise_ids)
    catalog = {
        exercise_id: (tracking_type, ai_algorithm)
        for exercise_id, tracking_type, ai_algorithm in db.execute(
            select(
                Exercise.exercise_id, Exercise.tracking_type, Exercise.ai_algorithm
            ).where(Exercise.exercise_id.in_(set(exercise_ids)))
        ).all()
    }

    sets_by_exercise: dict[int, list[SessionSetRead]] = defaultdict(list)
    for row in db.scalars(
        select(SessionSet)
        .where(SessionSet.session_exercise_id.in_(ids))
        .order_by(SessionSet.session_exercise_id, SessionSet.set_order)
    ).all():
        sets_by_exercise[row.session_exercise_id].append(_set_read(row))

    return [
        SessionExerciseRead(
            session_exercise_id=se.session_exercise_id,
            exercise_id=se.exercise_id,
            exercise_name=se.exercise_name,
            exercise_order=se.exercise_order,
            notes=se.notes,
            # The icon, falling back to the thumbnail - same rule as the lists.
            thumbnail_url=(
                images.get(se.exercise_id, (None, None))[0]
                or images.get(se.exercise_id, (None, None))[1]
            ),
            tracking_type=catalog.get(se.exercise_id, ("reps", None))[0],
            ai_algorithm=catalog.get(se.exercise_id, ("reps", None))[1],
            sets=sets_by_exercise.get(se.session_exercise_id, []),
        )
        for se in session_exercises
    ]


def build_detail(db: Session, session: WorkoutSession) -> WorkoutSessionDetail:
    exercises = db.scalars(
        select(SessionExercise)
        .where(SessionExercise.session_id == session.session_id)
        .order_by(SessionExercise.exercise_order)
    ).all()

    summary = summaries(db, [session.session_id]).get(
        session.session_id, EMPTY_SUMMARY
    )

    return WorkoutSessionDetail(
        session_id=session.session_id,
        workout_day_id=session.workout_day_id,
        plan_name=session.plan_name,
        day_name=session.day_name,
        scheduled_date=session.scheduled_date,
        started_at=session.started_at,
        completed_at=session.completed_at,
        status=session.status,
        duration_seconds=session.duration_seconds,
        paused_seconds=session.paused_seconds,
        notes=session.notes,
        summary=summary,
        exercises=build_exercise_reads(db, exercises),
    )
