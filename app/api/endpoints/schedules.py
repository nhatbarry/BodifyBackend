from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.schedules import Schedule
from app.models.workout_days import WorkoutDay
from app.models.workout_plans import WorkoutPlan
from app.schemas.common import Page
from app.schemas.schedules import (
    CalendarItem,
    CalendarResponse,
    ScheduleCreate,
    ScheduleRead,
    ScheduleUpdate,
)
from app.services.workout_access import (
    get_owned_day,
    get_owned_plan,
    get_owned_schedule,
)

router = APIRouter(prefix="/schedules", tags=["schedules"])

# A year of expansion is already more than any calendar screen shows, and it
# caps how many rows a single request can generate from one recurring rule.
MAX_CALENDAR_DAYS = 366


def _read(schedule: Schedule, day: WorkoutDay, plan: WorkoutPlan) -> ScheduleRead:
    result = ScheduleRead.model_validate(schedule)
    result.workout_day_name = day.name
    result.is_rest_day = day.is_rest_day
    result.plan_id = plan.plan_id
    result.plan_name = plan.name
    return result


def _duplicate(exc: IntegrityError) -> bool:
    return "uq_schedules_" in str(exc.orig)


@router.post("", response_model=ScheduleRead, status_code=status.HTTP_201_CREATED)
def create_schedule(
    payload: ScheduleCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ScheduleRead:
    """Schedule one of the caller's workout days.

    The day's ownership is checked here because the database cannot: a
    schedule row records its own user_id, and nothing at the SQL level ties
    that to the plan the day belongs to.
    """
    day = get_owned_day(db, payload.workout_day_id, current_user.id)
    plan = db.get(WorkoutPlan, day.plan_id)

    schedule = Schedule(
        user_id=current_user.id,
        workout_day_id=payload.workout_day_id,
        scheduled_date=payload.scheduled_date,
        day_of_week=payload.day_of_week,
        is_active=payload.is_active,
    )
    db.add(schedule)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _duplicate(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This day is already scheduled for that date or weekday",
            ) from exc
        raise
    db.refresh(schedule)
    return _read(schedule, day, plan)


@router.get("", response_model=Page[ScheduleRead])
def list_schedules(
    current_user: CurrentUserDep,
    db: DbSession,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    is_active: bool | None = Query(None),
) -> Page[ScheduleRead]:
    """The caller's schedule rules as stored - one-off dates and weekly
    repeats alike.

    This is the management view. It does not expand recurrence: a row saying
    day_of_week=1 is a rule, not a date. For something a calendar can render,
    use /schedules/calendar.
    """
    conditions = [Schedule.user_id == current_user.id]
    if is_active is not None:
        conditions.append(Schedule.is_active.is_(is_active))

    total = db.scalar(select(func.count()).select_from(Schedule).where(*conditions))

    rows = db.execute(
        select(Schedule, WorkoutDay, WorkoutPlan)
        .join(WorkoutDay, WorkoutDay.workout_day_id == Schedule.workout_day_id)
        .join(WorkoutPlan, WorkoutPlan.plan_id == WorkoutDay.plan_id)
        .where(*conditions)
        .order_by(
            Schedule.scheduled_date.asc().nulls_last(),
            Schedule.day_of_week.asc().nulls_last(),
            Schedule.schedule_id,
        )
        .limit(limit)
        .offset((page - 1) * limit)
    ).all()

    return Page[ScheduleRead](
        items=[_read(schedule, day, plan) for schedule, day, plan in rows],
        page=page,
        limit=limit,
        total=total or 0,
    )


# Declared before /{schedule_id}: FastAPI matches in order and would otherwise
# try to read "calendar" as an integer.
@router.get("/calendar", response_model=CalendarResponse)
def get_calendar(
    current_user: CurrentUserDep,
    db: DbSession,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
    plan_id: int | None = Query(
        None, description="Restrict to one plan; omit for every plan being followed"
    ),
) -> CalendarResponse:
    """Real dates for a range, with weekly rules already expanded.

    `items` holds only dates that have a schedule, which leaves the client
    three states to render: an item with is_rest_day false is training, an
    item with is_rest_day true is a planned rest, and a date that appears at
    all is simply unplanned.

    Without `plan_id` this answers for every plan the user has not archived.
    Pass `plan_id` to render one plan's own calendar - that view is scoped to
    the plan the user opened, so its status is not second-guessed and an
    archived plan still shows its dates.
    """
    if to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must not be earlier than 'from'",
        )
    if (to_date - from_date).days + 1 > MAX_CALENDAR_DAYS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Range must not exceed {MAX_CALENDAR_DAYS} days",
        )

    conditions = [
        Schedule.user_id == current_user.id,
        Schedule.is_active.is_(True),
    ]
    if plan_id is None:
        # Archiving a plan is how a user says they no longer follow it, so it
        # drops out of the combined view. Its rows stay in GET /schedules.
        conditions.append(WorkoutPlan.status == "active")
    else:
        get_owned_plan(db, plan_id, current_user.id)
        conditions.append(WorkoutPlan.plan_id == plan_id)

    rows = db.execute(
        select(Schedule, WorkoutDay, WorkoutPlan)
        .join(WorkoutDay, WorkoutDay.workout_day_id == Schedule.workout_day_id)
        .join(WorkoutPlan, WorkoutPlan.plan_id == WorkoutDay.plan_id)
        .where(*conditions)
    ).all()

    items: list[CalendarItem] = []
    for schedule, day, plan in rows:
        if schedule.scheduled_date is not None:
            occurrences = (
                [schedule.scheduled_date]
                if from_date <= schedule.scheduled_date <= to_date
                else []
            )
        else:
            # isoweekday(): 1 = Monday .. 7 = Sunday, the same convention the
            # day_of_week column uses.
            shift = (schedule.day_of_week - from_date.isoweekday()) % 7
            occurrences = []
            current = from_date + timedelta(days=shift)
            while current <= to_date:
                occurrences.append(current)
                current += timedelta(days=7)

        items.extend(
            CalendarItem(
                date=occurrence,
                schedule_id=schedule.schedule_id,
                workout_day_id=day.workout_day_id,
                title=day.name,
                is_rest_day=day.is_rest_day,
                plan_id=plan.plan_id,
                plan_name=plan.name,
            )
            for occurrence in occurrences
        )

    items.sort(key=lambda item: (item.date, item.workout_day_id))
    return CalendarResponse(from_date=from_date, to_date=to_date, items=items)


@router.put("/{schedule_id}", response_model=ScheduleRead)
def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ScheduleRead:
    """Move a schedule, or switch it between one-off and recurring.

    Setting one of the two target columns clears the other, since the database
    requires exactly one of them.
    """
    schedule = get_owned_schedule(db, schedule_id, current_user.id)
    changes = payload.model_dump(exclude_unset=True)

    if "workout_day_id" in changes:
        if changes["workout_day_id"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="workout_day_id cannot be null",
            )
        # Re-checked: the new day must also belong to the caller.
        get_owned_day(db, changes["workout_day_id"], current_user.id)
        schedule.workout_day_id = changes["workout_day_id"]

    if "is_active" in changes:
        if changes["is_active"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="is_active cannot be null",
            )
        schedule.is_active = changes["is_active"]

    if changes.get("scheduled_date") is not None:
        schedule.scheduled_date = changes["scheduled_date"]
        schedule.day_of_week = None
    elif changes.get("day_of_week") is not None:
        schedule.day_of_week = changes["day_of_week"]
        schedule.scheduled_date = None
    elif "scheduled_date" in changes or "day_of_week" in changes:
        # Both keys present and both null, or the only target was cleared.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide exactly one of scheduled_date or day_of_week",
        )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _duplicate(exc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This day is already scheduled for that date or weekday",
            ) from exc
        raise
    db.refresh(schedule)

    day = db.get(WorkoutDay, schedule.workout_day_id)
    plan = db.get(WorkoutPlan, day.plan_id)
    return _read(schedule, day, plan)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Removes the schedule only - the workout day and its plan are untouched."""
    schedule = get_owned_schedule(db, schedule_id, current_user.id)
    db.delete(schedule)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
