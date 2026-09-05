from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select, update

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.exercises import Exercise
from app.models.workout_days import WorkoutDay
from app.models.workout_plans import WorkoutPlan
from app.schemas.common import Page
from app.schemas.workout_plans import (
    DateExercisesRequest,
    DateRestDayRequest,
    DayReorderRequest,
    PlanActiveRequest,
    WorkoutDayCreate,
    WorkoutDayDetail,
    WorkoutPlanCreate,
    WorkoutPlanDetail,
    WorkoutPlanListItem,
    WorkoutPlanUpdate,
)
from app.services.exercise_translation import DEFAULT_LANG
from app.services.workout_access import get_owned_plan
from app.services.workout_dates import (
    MAX_CONSECUTIVE_REST_DAYS,
    TooManyRestDays,
)
from app.services.workout_dates import (
    find_day_for_date,
    replace_date_exercises,
    set_date_rest_day,
)
from app.services.workout_ordering import apply_reorder, lock_parent, next_order
from app.services.workout_plan_view import (
    build_exercise_reads,
    build_plan_detail,
    build_plan_list,
)

router = APIRouter(tags=["workout-plans"])


@router.post(
    "/workout-plans",
    response_model=WorkoutPlanDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_workout_plan(
    payload: WorkoutPlanCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutPlanDetail:
    """Create an empty plan. user_id comes from the token, never the body."""
    plan = WorkoutPlan(
        user_id=current_user.id,
        name=payload.name,
        description=payload.description,
        goal=payload.goal,
        duration_weeks=payload.duration_weeks,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return build_plan_detail(db, plan, DEFAULT_LANG)


@router.get("/workout-plans", response_model=Page[WorkoutPlanListItem])
def list_workout_plans(
    current_user: CurrentUserDep,
    db: DbSession,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(
        None, alias="status", description="active or archived"
    ),
) -> Page[WorkoutPlanListItem]:
    """The caller's own plans, each with a shallow preview of its days.

    Stops above the exercises on purpose: nesting the full tree here would
    load every set of every plan to render a screen that shows plan names.
    """
    conditions = [WorkoutPlan.user_id == current_user.id]
    if status_filter:
        conditions.append(WorkoutPlan.status == status_filter)

    total = db.scalar(
        select(func.count()).select_from(WorkoutPlan).where(*conditions)
    )
    plans = db.scalars(
        select(WorkoutPlan)
        .where(*conditions)
        .order_by(WorkoutPlan.created_at.desc(), WorkoutPlan.plan_id.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    ).all()

    return Page[WorkoutPlanListItem](
        items=build_plan_list(db, plans), page=page, limit=limit, total=total or 0
    )


@router.get("/workout-plans/{plan_id}", response_model=WorkoutPlanDetail)
def get_workout_plan(
    plan_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutPlanDetail:
    """The full tree, so the planner screen needs one call rather than five."""
    plan = get_owned_plan(db, plan_id, current_user.id)
    return build_plan_detail(db, plan, language)


@router.put("/workout-plans/{plan_id}", response_model=WorkoutPlanDetail)
def update_workout_plan(
    plan_id: int,
    payload: WorkoutPlanUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutPlanDetail:
    """Omitting a key keeps its value; sending null clears it.

    This is also how a plan is archived - status='archived' keeps every row,
    unlike DELETE.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)
    changes = payload.model_dump(exclude_unset=True)

    if "name" in changes and changes["name"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="name cannot be null",
        )
    if "status" in changes and changes["status"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status cannot be null",
        )

    for field, value in changes.items():
        setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return build_plan_detail(db, plan, language)


@router.delete("/workout-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workout_plan(
    plan_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Permanent. ON DELETE CASCADE takes the plan's days, exercises, target
    sets and schedules with it, and there is no soft delete to fall back on -
    use PUT status='archived' to keep the data.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)
    db.delete(plan)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workout-plans/{plan_id}/days",
    response_model=WorkoutDayDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_workout_day(
    plan_id: int,
    payload: WorkoutDayCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutDayDetail:
    """Add a day. Omit day_order to append at the end of the plan."""
    plan = get_owned_plan(db, plan_id, current_user.id)

    if payload.day_order is None:
        # Lock the plan row first: two concurrent appends would otherwise read
        # the same MAX(day_order) and collide on the unique constraint.
        lock_parent(db, WorkoutPlan.plan_id, plan.plan_id)
        day_order = next_order(db, WorkoutDay.day_order, WorkoutDay.plan_id == plan_id)
    else:
        day_order = payload.day_order

    day = WorkoutDay(
        plan_id=plan_id,
        name=payload.name,
        day_order=day_order,
        is_rest_day=payload.is_rest_day,
        description=payload.description,
    )
    db.add(day)
    db.commit()
    db.refresh(day)

    return WorkoutDayDetail(
        workout_day_id=day.workout_day_id,
        name=day.name,
        day_order=day.day_order,
        is_rest_day=day.is_rest_day,
        description=day.description,
        exercises=[],
    )


@router.put("/workout-plans/{plan_id}/days/reorder", response_model=WorkoutPlanDetail)
def reorder_workout_days(
    plan_id: int,
    payload: DayReorderRequest,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutPlanDetail:
    """Rewrite the order of every day in the plan, in one transaction.

    Not expressible as PUT /workout-days/{id}: separate requests commit
    separately, so the first move onto an occupied position fails the unique
    constraint even though the end state would be valid.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)

    apply_reorder(
        db,
        model=WorkoutDay,
        pk_column=WorkoutDay.workout_day_id,
        order_column=WorkoutDay.day_order,
        parent_condition=WorkoutDay.plan_id == plan_id,
        requested={item.workout_day_id: item.day_order for item in payload.items},
        label="plan",
    )
    db.commit()
    return build_plan_detail(db, plan, language)


@router.put("/workout-plans/{plan_id}/active", response_model=WorkoutPlanDetail)
def set_plan_active(
    plan_id: int,
    payload: PlanActiveRequest,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutPlanDetail:
    """Choose which plan the user is currently following.

    A user may keep many plans but follows one, so activating a plan
    deactivates whatever was active before. Both statements run in this one
    transaction, and the clear has to come first: `uq_workout_plans_one_active`
    is a plain unique index, so two rows must never be true at the same time,
    not even between statements.

    Setting is_active false simply leaves the user following nothing.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)

    if payload.is_active:
        db.execute(
            update(WorkoutPlan)
            .where(
                WorkoutPlan.user_id == current_user.id,
                WorkoutPlan.is_active.is_(True),
            )
            .values(is_active=False)
        )
        db.flush()

    plan.is_active = payload.is_active
    db.commit()
    db.refresh(plan)
    return build_plan_detail(db, plan, language)


@router.get(
    "/workout-plans/{plan_id}/dates/{on_date}", response_model=WorkoutDayDetail | None
)
def get_plan_date(
    plan_id: int,
    on_date: date,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutDayDetail | None:
    """What this plan has on one calendar date.

    Answers null rather than 404 when the date is empty: "nothing planned" is
    an ordinary state for a date the user is browsing, not a missing resource.
    """
    get_owned_plan(db, plan_id, current_user.id)
    day = find_day_for_date(db, plan_id, on_date)
    if day is None:
        return None

    exercises = sorted(day.exercises, key=lambda we: we.exercise_order)
    return WorkoutDayDetail(
        workout_day_id=day.workout_day_id,
        name=day.name,
        day_order=day.day_order,
        is_rest_day=day.is_rest_day,
        description=day.description,
        exercises=build_exercise_reads(db, exercises, language),
    )


@router.put(
    "/workout-plans/{plan_id}/dates/{on_date}/exercises",
    response_model=WorkoutDayDetail | None,
)
def set_plan_date_exercises(
    plan_id: int,
    on_date: date,
    payload: DateExercisesRequest,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutDayDetail | None:
    """Set this date's exercise list to exactly what the picker returned.

    The picker shows every exercise already on the date as ticked, so the
    request is the whole list and unticking is how something is removed.
    An empty list clears the date and answers null.

    Exercises that survive keep their existing rows, and with them their
    target sets - only the ones actually unticked are deleted.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)

    if payload.exercise_ids:
        # Verified up front so a bad id fails the whole request rather than
        # tripping the foreign key halfway through as an opaque 500.
        found = set(
            db.scalars(
                select(Exercise.exercise_id).where(
                    Exercise.exercise_id.in_(set(payload.exercise_ids))
                )
            ).all()
        )
        missing = sorted(set(payload.exercise_ids) - found)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Exercise not found: {missing}",
            )

    day = replace_date_exercises(
        db, plan, current_user.id, on_date, payload.exercise_ids
    )
    db.commit()

    if day is None:
        return None

    db.refresh(day)
    exercises = sorted(day.exercises, key=lambda we: we.exercise_order)
    return WorkoutDayDetail(
        workout_day_id=day.workout_day_id,
        name=day.name,
        day_order=day.day_order,
        is_rest_day=day.is_rest_day,
        description=day.description,
        exercises=build_exercise_reads(db, exercises, language),
    )


@router.put(
    "/workout-plans/{plan_id}/dates/{on_date}/rest",
    response_model=WorkoutDayDetail | None,
)
def set_plan_date_rest(
    plan_id: int,
    on_date: date,
    payload: DateRestDayRequest,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutDayDetail | None:
    """Mark a calendar date as a planned rest day, or clear the mark.

    Ticking an empty date creates the day and its schedule row, because a rest
    day is a row rather than an absence - that is what lets the calendar tell
    "resting today" from "nothing scheduled yet".

    Unticking a rest day removes it again: a day with no exercises and no rest
    flag means nothing, and would show on the calendar as an empty workout.

    Answers null when the date ends up with nothing on it, matching
    GET /workout-plans/{id}/dates/{date}.
    """
    plan = get_owned_plan(db, plan_id, current_user.id)

    try:
        day = set_date_rest_day(db, plan, current_user.id, on_date, payload.is_rest_day)
    except TooManyRestDays as too_many:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A plan cannot have more than {MAX_CONSECUTIVE_REST_DAYS} "
                f"rest days in a row (this would make {too_many.run_length})"
            ),
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove this date's exercises before marking it a rest day",
        ) from None

    db.commit()
    if day is None:
        return None
    db.refresh(day)

    return WorkoutDayDetail(
        workout_day_id=day.workout_day_id,
        name=day.name,
        day_order=day.day_order,
        is_rest_day=day.is_rest_day,
        description=day.description,
        exercises=[],
    )
