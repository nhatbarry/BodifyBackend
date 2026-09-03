from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.exercises import Exercise
from app.models.workout_days import WorkoutDay
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.workout_exercises import WorkoutExercise
from app.schemas.workout_plans import (
    ExerciseReorderRequest,
    WorkoutDayDetail,
    WorkoutDayUpdate,
    WorkoutExerciseCreate,
    WorkoutExerciseRead,
)
from app.services.exercise_translation import DEFAULT_LANG
from app.services.workout_access import get_owned_day
from app.services.workout_ordering import apply_reorder, lock_parent, next_order
from app.services.workout_plan_view import build_exercise_reads

router = APIRouter(prefix="/workout-days", tags=["workout-days"])


def _day_detail(db, day: WorkoutDay, language: str) -> WorkoutDayDetail:
    exercises = sorted(day.exercises, key=lambda we: we.exercise_order)
    return WorkoutDayDetail(
        workout_day_id=day.workout_day_id,
        name=day.name,
        day_order=day.day_order,
        is_rest_day=day.is_rest_day,
        description=day.description,
        exercises=build_exercise_reads(db, exercises, language),
    )


@router.put("/{workout_day_id}", response_model=WorkoutDayDetail)
def update_workout_day(
    workout_day_id: int,
    payload: WorkoutDayUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutDayDetail:
    """Rename a day, change its description, or mark it a rest day.

    day_order is not settable here - see PUT /workout-plans/{id}/days/reorder.
    """
    day = get_owned_day(db, workout_day_id, current_user.id)
    changes = payload.model_dump(exclude_unset=True)

    for field in ("name", "is_rest_day"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field} cannot be null",
            )

    # Turning a populated day into a rest day would leave exercises attached to
    # a day that is supposed to have none - a state nothing else in the API can
    # produce. Make the caller clear them first rather than deleting their work.
    if changes.get("is_rest_day") and day.exercises:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove this day's exercises before marking it a rest day",
        )

    for field, value in changes.items():
        setattr(day, field, value)

    db.commit()
    db.refresh(day)
    return _day_detail(db, day, language)


@router.delete("/{workout_day_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workout_day(
    workout_day_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Removes the day along with its exercises, target sets and any schedule
    rows pointing at it."""
    day = get_owned_day(db, workout_day_id, current_user.id)
    db.delete(day)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{workout_day_id}/exercises",
    response_model=WorkoutExerciseRead,
    status_code=status.HTTP_201_CREATED,
)
def add_exercise_to_day(
    workout_day_id: int,
    payload: WorkoutExerciseCreate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutExerciseRead:
    """Place a catalog exercise in this day. Omit exercise_order to append."""
    day = get_owned_day(db, workout_day_id, current_user.id)

    if day.is_rest_day:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot add exercises to a rest day",
        )

    # Checked here rather than left to the foreign key: a RESTRICT violation
    # surfaces as an opaque 500, and "exercise must exist" is a stated rule.
    if db.get(Exercise, payload.exercise_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found"
        )

    if payload.exercise_order is None:
        lock_parent(db, WorkoutDay.workout_day_id, workout_day_id)
        exercise_order = next_order(
            db,
            WorkoutExercise.exercise_order,
            WorkoutExercise.workout_day_id == workout_day_id,
        )
    else:
        exercise_order = payload.exercise_order

    workout_exercise = WorkoutExercise(
        workout_day_id=workout_day_id,
        exercise_id=payload.exercise_id,
        exercise_order=exercise_order,
        notes=payload.notes,
    )
    db.add(workout_exercise)
    db.flush()

    # Target sets may come along in the same request; positions given here are
    # sequential by construction, so no lock is needed.
    for position, target_set in enumerate(payload.sets or [], start=1):
        db.add(
            WorkoutExerciseSet(
                workout_exercise_id=workout_exercise.workout_exercise_id,
                set_order=target_set.set_order or position,
                target_reps=target_set.target_reps,
                target_weight_kg=target_set.target_weight_kg,
                rest_seconds=target_set.rest_seconds,
                notes=target_set.notes,
            )
        )

    db.commit()
    db.refresh(workout_exercise)
    return build_exercise_reads(db, [workout_exercise], language)[0]


@router.put("/{workout_day_id}/exercises/reorder", response_model=WorkoutDayDetail)
def reorder_day_exercises(
    workout_day_id: int,
    payload: ExerciseReorderRequest,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutDayDetail:
    """Rewrite the order of every exercise in this day, in one transaction."""
    day = get_owned_day(db, workout_day_id, current_user.id)

    apply_reorder(
        db,
        model=WorkoutExercise,
        pk_column=WorkoutExercise.workout_exercise_id,
        order_column=WorkoutExercise.exercise_order,
        parent_condition=WorkoutExercise.workout_day_id == workout_day_id,
        requested={
            item.workout_exercise_id: item.exercise_order for item in payload.items
        },
        label="day",
    )
    db.commit()
    db.refresh(day)
    return _day_detail(db, day, language)
