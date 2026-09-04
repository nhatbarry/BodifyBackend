from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_sessions import ACTIVE_STATUSES
from app.schemas.workout_sessions import SessionSetCreate, SessionSetRead, SessionSetUpdate
from app.services.workout_access import (
    get_owned_session,
    get_owned_session_exercise,
    get_owned_session_set,
)
from app.services.workout_ordering import lock_parent, next_order

router = APIRouter(tags=["session-sets"])


def _read(row: SessionSet) -> SessionSetRead:
    result = SessionSetRead.model_validate(row)
    result.volume_kg = row.volume_kg
    return result


@router.post(
    "/session-exercises/{session_exercise_id}/sets",
    response_model=SessionSetRead,
    status_code=status.HTTP_201_CREATED,
)
def log_set(
    session_exercise_id: int,
    payload: SessionSetCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> SessionSetRead:
    """Record one set as it is performed. Omit set_order to append."""
    session_exercise = get_owned_session_exercise(
        db, session_exercise_id, current_user.id
    )
    session = get_owned_session(db, session_exercise.session_id, current_user.id)

    # Paused counts as active: the clock has stopped, the workout has not, and
    # refusing to record a set the user just did would be perverse.
    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session is {session.status}; no new sets can be added. "
                "Existing sets can still be corrected."
            ),
        )

    if payload.set_order is None:
        # Two phones logging the same exercise would otherwise both read the
        # same MAX(set_order) and collide.
        lock_parent(db, SessionExercise.session_exercise_id, session_exercise_id)
        set_order = next_order(
            db,
            SessionSet.set_order,
            SessionSet.session_exercise_id == session_exercise_id,
        )
    else:
        set_order = payload.set_order

    performed = SessionSet(
        session_exercise_id=session_exercise_id,
        set_order=set_order,
        reps=payload.reps,
        weight_kg=payload.weight_kg,
        duration_seconds=payload.duration_seconds,
        distance_m=payload.distance_m,
        rest_seconds=payload.rest_seconds,
        completed=payload.completed,
        target_reps=payload.target_reps,
        target_weight_kg=payload.target_weight_kg,
        target_duration_seconds=payload.target_duration_seconds,
        target_distance_m=payload.target_distance_m,
        workout_exercise_set_id=payload.workout_exercise_set_id,
        notes=payload.notes,
    )
    db.add(performed)
    db.commit()
    db.refresh(performed)
    return _read(performed)


@router.put("/session-sets/{session_set_id}", response_model=SessionSetRead)
def correct_set(
    session_set_id: int,
    payload: SessionSetUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> SessionSetRead:
    """Correct a set that was already recorded.

    This is the one write a finished session still accepts: mistyping 40 kg as
    400 is common, and a history nobody can fix is worse than one that can be.
    What it will not do is revise the `target_*` snapshot - those record what
    the plan said at the time, which is not the user's to change afterwards -
    and it will not move the set to another exercise.

    Omitting a key keeps its value; sending null clears it.
    """
    performed = get_owned_session_set(db, session_set_id, current_user.id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "completed" and value is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="completed cannot be null",
            )
        setattr(performed, field, value)

    db.commit()
    db.refresh(performed)
    return _read(performed)


@router.delete(
    "/session-sets/{session_set_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_set(
    session_set_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Remove a set that should not have been recorded.

    Allowed on a finished session for the same reason PUT is: a set logged by
    a mis-tap is not history, it is a typo, and the alternative is a permanent
    wrong number. What stays impossible is adding to a finished session.

    The positions of the remaining sets are left alone. Renumbering would
    silently move rows the user is looking at, and a gap in set_order harms
    nothing.
    """
    performed = get_owned_session_set(db, session_set_id, current_user.id)
    db.delete(performed)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/session-exercises/{session_exercise_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_session_exercise(
    session_exercise_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Remove an exercise from a session, and every set recorded under it."""
    session_exercise = get_owned_session_exercise(
        db, session_exercise_id, current_user.id
    )
    db.delete(session_exercise)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
