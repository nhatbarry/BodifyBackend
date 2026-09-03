"""Ownership checks for the Phase 2 planning tables.

The database cannot express these. `workout_plans.user_id` is the only place
an owner is recorded, and days, exercises and sets sit one, two and three
joins below it; `schedules` records a `user_id` of its own but nothing stops
it pointing at somebody else's day. So every route that touches a planning row
resolves it through this module first.

Ownership is folded into the WHERE clause rather than checked after loading,
so a row belonging to another user is indistinguishable from one that does not
exist: both answer 404. Answering 403 would confirm the id is real - the same
rule body_metrics follows.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.schedules import Schedule
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_sessions import WorkoutSession
from app.models.workout_days import WorkoutDay
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan


def _not_found(what: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{what} not found"
    )


def get_owned_plan(db: Session, plan_id: int, user_id: uuid.UUID) -> WorkoutPlan:
    plan = db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.plan_id == plan_id,
            WorkoutPlan.user_id == user_id,
        )
    )
    if plan is None:
        raise _not_found("Workout plan")
    return plan


def get_owned_day(db: Session, workout_day_id: int, user_id: uuid.UUID) -> WorkoutDay:
    day = db.scalar(
        select(WorkoutDay)
        .join(WorkoutPlan, WorkoutPlan.plan_id == WorkoutDay.plan_id)
        .where(
            WorkoutDay.workout_day_id == workout_day_id,
            WorkoutPlan.user_id == user_id,
        )
    )
    if day is None:
        raise _not_found("Workout day")
    return day


def get_owned_exercise(
    db: Session, workout_exercise_id: int, user_id: uuid.UUID
) -> WorkoutExercise:
    workout_exercise = db.scalar(
        select(WorkoutExercise)
        .join(WorkoutDay, WorkoutDay.workout_day_id == WorkoutExercise.workout_day_id)
        .join(WorkoutPlan, WorkoutPlan.plan_id == WorkoutDay.plan_id)
        .where(
            WorkoutExercise.workout_exercise_id == workout_exercise_id,
            WorkoutPlan.user_id == user_id,
        )
    )
    if workout_exercise is None:
        raise _not_found("Workout exercise")
    return workout_exercise


def get_owned_set(
    db: Session, workout_exercise_set_id: int, user_id: uuid.UUID
) -> WorkoutExerciseSet:
    target_set = db.scalar(
        select(WorkoutExerciseSet)
        .join(
            WorkoutExercise,
            WorkoutExercise.workout_exercise_id
            == WorkoutExerciseSet.workout_exercise_id,
        )
        .join(WorkoutDay, WorkoutDay.workout_day_id == WorkoutExercise.workout_day_id)
        .join(WorkoutPlan, WorkoutPlan.plan_id == WorkoutDay.plan_id)
        .where(
            WorkoutExerciseSet.workout_exercise_set_id == workout_exercise_set_id,
            WorkoutPlan.user_id == user_id,
        )
    )
    if target_set is None:
        raise _not_found("Workout exercise set")
    return target_set


def get_owned_schedule(db: Session, schedule_id: int, user_id: uuid.UUID) -> Schedule:
    schedule = db.scalar(
        select(Schedule).where(
            Schedule.schedule_id == schedule_id,
            Schedule.user_id == user_id,
        )
    )
    if schedule is None:
        raise _not_found("Schedule")
    return schedule


# ============================================================
# Phase 3 - sessions
# ============================================================
# A session records its own user_id directly, so its own lookup is a plain
# WHERE. Its children are not: a set is two joins from the owner, and the same
# 404-not-403 rule applies to all of them.


def get_owned_session(
    db: Session, session_id: int, user_id: uuid.UUID
) -> WorkoutSession:
    session = db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.session_id == session_id,
            WorkoutSession.user_id == user_id,
        )
    )
    if session is None:
        raise _not_found("Workout session")
    return session


def get_owned_session_exercise(
    db: Session, session_exercise_id: int, user_id: uuid.UUID
) -> SessionExercise:
    session_exercise = db.scalar(
        select(SessionExercise)
        .join(
            WorkoutSession,
            WorkoutSession.session_id == SessionExercise.session_id,
        )
        .where(
            SessionExercise.session_exercise_id == session_exercise_id,
            WorkoutSession.user_id == user_id,
        )
    )
    if session_exercise is None:
        raise _not_found("Session exercise")
    return session_exercise


def get_owned_session_set(
    db: Session, session_set_id: int, user_id: uuid.UUID
) -> SessionSet:
    session_set = db.scalar(
        select(SessionSet)
        .join(
            SessionExercise,
            SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
        )
        .join(
            WorkoutSession,
            WorkoutSession.session_id == SessionExercise.session_id,
        )
        .where(
            SessionSet.session_set_id == session_set_id,
            WorkoutSession.user_id == user_id,
        )
    )
    if session_set is None:
        raise _not_found("Session set")
    return session_set
