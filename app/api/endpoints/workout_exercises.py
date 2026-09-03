from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.workout_exercises import WorkoutExercise
from app.schemas.workout_plans import (
    SetReorderRequest,
    WorkoutExerciseRead,
    WorkoutExerciseUpdate,
    WorkoutSetCreate,
    WorkoutSetRead,
)
from app.services.exercise_translation import DEFAULT_LANG
from app.services.workout_access import get_owned_exercise
from app.services.workout_ordering import apply_reorder, lock_parent, next_order
from app.services.workout_plan_view import build_exercise_reads

router = APIRouter(prefix="/workout-exercises", tags=["workout-exercises"])


@router.put("/{workout_exercise_id}", response_model=WorkoutExerciseRead)
def update_workout_exercise(
    workout_exercise_id: int,
    payload: WorkoutExerciseUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutExerciseRead:
    """Metadata only.

    exercise_order is intentionally not accepted here. Moving one row at a
    time cannot express a swap: each request is its own transaction, so the
    first one to land on an occupied position violates the unique constraint.
    Use PUT /workout-days/{id}/exercises/reorder.
    """
    workout_exercise = get_owned_exercise(db, workout_exercise_id, current_user.id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(workout_exercise, field, value)

    db.commit()
    db.refresh(workout_exercise)
    return build_exercise_reads(db, [workout_exercise], language)[0]


@router.delete("/{workout_exercise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workout_exercise(
    workout_exercise_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Removes the exercise and its target sets. Positions of the remaining
    exercises are left as they are - gaps are harmless, and silently
    renumbering would fight whatever order the client is showing."""
    workout_exercise = get_owned_exercise(db, workout_exercise_id, current_user.id)
    db.delete(workout_exercise)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{workout_exercise_id}/sets",
    response_model=WorkoutSetRead,
    status_code=status.HTTP_201_CREATED,
)
def add_target_set(
    workout_exercise_id: int,
    payload: WorkoutSetCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSetRead:
    """Add one planned set. Omit set_order to append."""
    get_owned_exercise(db, workout_exercise_id, current_user.id)

    if payload.set_order is None:
        lock_parent(db, WorkoutExercise.workout_exercise_id, workout_exercise_id)
        set_order = next_order(
            db,
            WorkoutExerciseSet.set_order,
            WorkoutExerciseSet.workout_exercise_id == workout_exercise_id,
        )
    else:
        set_order = payload.set_order

    target_set = WorkoutExerciseSet(
        workout_exercise_id=workout_exercise_id,
        set_order=set_order,
        target_reps=payload.target_reps,
        target_weight_kg=payload.target_weight_kg,
        rest_seconds=payload.rest_seconds,
        notes=payload.notes,
    )
    db.add(target_set)
    db.commit()
    db.refresh(target_set)
    return WorkoutSetRead.model_validate(target_set)


@router.put("/{workout_exercise_id}/sets/reorder", response_model=WorkoutExerciseRead)
def reorder_target_sets(
    workout_exercise_id: int,
    payload: SetReorderRequest,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutExerciseRead:
    """Rewrite the order of every target set on this exercise, in one
    transaction."""
    workout_exercise = get_owned_exercise(db, workout_exercise_id, current_user.id)

    apply_reorder(
        db,
        model=WorkoutExerciseSet,
        pk_column=WorkoutExerciseSet.workout_exercise_set_id,
        order_column=WorkoutExerciseSet.set_order,
        parent_condition=WorkoutExerciseSet.workout_exercise_id == workout_exercise_id,
        requested={
            item.workout_exercise_set_id: item.set_order for item in payload.items
        },
        label="exercise",
    )
    db.commit()
    db.refresh(workout_exercise)
    return build_exercise_reads(db, [workout_exercise], language)[0]
