from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUserDep, DbSession
from app.schemas.workout_plans import WorkoutSetRead, WorkoutSetUpdate
from app.services.workout_access import get_owned_set

router = APIRouter(prefix="/workout-exercise-sets", tags=["workout-exercise-sets"])


@router.put("/{workout_exercise_set_id}", response_model=WorkoutSetRead)
def update_target_set(
    workout_exercise_set_id: int,
    payload: WorkoutSetUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSetRead:
    """Change a planned set's targets.

    Omitting a key keeps its value; sending null clears it - so a set can be
    reduced to "10 reps, weight up to you" by sending target_weight_kg: null.

    set_order is not accepted here; see PUT /workout-exercises/{id}/sets/reorder.
    """
    target_set = get_owned_set(db, workout_exercise_set_id, current_user.id)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target_set, field, value)

    db.commit()
    db.refresh(target_set)
    return WorkoutSetRead.model_validate(target_set)


@router.delete(
    "/{workout_exercise_set_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_target_set(
    workout_exercise_set_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    target_set = get_owned_set(db, workout_exercise_set_id, current_user.id)
    db.delete(target_set)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
