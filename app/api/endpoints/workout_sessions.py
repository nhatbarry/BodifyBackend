from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, select

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.exercises import Exercise
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_days import WorkoutDay
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan
from app.models.workout_sessions import (
    ACTIVE_STATUSES,
    COMPLETED,
    IN_PROGRESS,
    PAUSED,
    WorkoutSession,
)
from app.services.achievements import evaluate as evaluate_achievements
from app.services.personal_records import record_personal_bests
from app.schemas.common import Page
from app.schemas.workout_sessions import (
    HistoryTotals,
    SessionExerciseCreate,
    SessionExerciseRead,
    WorkoutHistoryResponse,
    WorkoutSessionCreate,
    WorkoutSessionDetail,
    WorkoutSessionFinish,
    WorkoutSessionListItem,
)
from app.services.exercise_translation import DEFAULT_LANG, best_translation
from app.services.workout_access import get_owned_day, get_owned_session
from app.services.session_view import build_detail, build_exercise_reads, build_list_items
from app.services.workout_ordering import lock_parent, next_order

router = APIRouter(tags=["workout-sessions"])


def _exercise_names(db, exercise_ids: list[int], language: str) -> dict[int, str]:
    """Catalog names, for snapshotting into the session rows."""
    if not exercise_ids:
        return {}
    translation = best_translation(language)
    return {
        exercise_id: name
        for exercise_id, name in db.execute(
            select(translation.c.exercise_id, translation.c.name).where(
                translation.c.exercise_id.in_(set(exercise_ids))
            )
        ).all()
        if name
    }


def _reject_if_finished(session: WorkoutSession) -> None:
    """Structural writes need an active session.

    Not a dead end: POST /workout-sessions/{id}/reopen puts a finished session
    back in progress, so "I did one more set after hitting finish" is a
    reopen, not a second workout. The refusal here keeps the two steps
    explicit rather than silently un-finishing a session behind a set write.
    Correcting a set that was already recorded never needed either - see
    PUT /session-sets/{id}.
    """
    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session is {session.status}; reopen it to add to it. "
                "Existing sets can still be corrected."
            ),
        )


@router.post(
    "/workout-sessions",
    response_model=WorkoutSessionDetail,
    status_code=status.HTTP_201_CREATED,
)
def start_workout_session(
    payload: WorkoutSessionCreate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutSessionDetail:
    """Start a workout.

    Given a `workout_day_id`, the plan and day names are snapshotted and the
    day's exercises and target sets are copied in, so recording a planned
    workout is one call rather than one per set. Set `copy_plan` false to start
    from the day but log it freehand.
    """
    existing = db.scalar(
        select(WorkoutSession).where(
            WorkoutSession.user_id == current_user.id,
            WorkoutSession.status.in_(ACTIVE_STATUSES),
        )
    )
    if existing is not None:
        # Reported explicitly rather than left to the partial unique index, so
        # the client can offer to resume instead of showing a database error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A workout is already in progress (session {existing.session_id}). "
                "Finish or abandon it first."
            ),
        )

    day: WorkoutDay | None = None
    plan: WorkoutPlan | None = None
    if payload.workout_day_id is not None:
        day = get_owned_day(db, payload.workout_day_id, current_user.id)
        plan = db.get(WorkoutPlan, day.plan_id)

    session = WorkoutSession(
        user_id=current_user.id,
        workout_day_id=day.workout_day_id if day else None,
        plan_name=plan.name if plan else None,
        day_name=day.name if day else None,
        scheduled_date=payload.scheduled_date or date.today(),
        notes=payload.notes,
        status=IN_PROGRESS,
    )
    if payload.started_at is not None:
        session.started_at = payload.started_at

    db.add(session)
    db.flush()

    if day is not None and payload.copy_plan:
        _copy_plan_into_session(db, session, day, language)

    db.commit()
    db.refresh(session)
    return build_detail(db, session)


def _copy_plan_into_session(
    db, session: WorkoutSession, day: WorkoutDay, language: str
) -> None:
    """Seed the session from the planned day.

    The plan's numbers land in the `target_*` columns, and the performed
    columns are left empty - nothing has been lifted yet. Copying rather than
    referencing is what keeps the history stable when the plan is edited later.
    """
    planned = db.scalars(
        select(WorkoutExercise)
        .where(WorkoutExercise.workout_day_id == day.workout_day_id)
        .order_by(WorkoutExercise.exercise_order)
    ).all()
    if not planned:
        return

    names = _exercise_names(db, [we.exercise_id for we in planned], language)

    target_sets = db.scalars(
        select(WorkoutExerciseSet)
        .where(
            WorkoutExerciseSet.workout_exercise_id.in_(
                [we.workout_exercise_id for we in planned]
            )
        )
        .order_by(WorkoutExerciseSet.workout_exercise_id, WorkoutExerciseSet.set_order)
    ).all()
    by_exercise: dict[int, list[WorkoutExerciseSet]] = {}
    for target in target_sets:
        by_exercise.setdefault(target.workout_exercise_id, []).append(target)

    for position, planned_exercise in enumerate(planned, start=1):
        session_exercise = SessionExercise(
            session_id=session.session_id,
            exercise_id=planned_exercise.exercise_id,
            exercise_name=names.get(planned_exercise.exercise_id),
            exercise_order=position,
            notes=planned_exercise.notes,
        )
        db.add(session_exercise)
        db.flush()

        for set_position, target in enumerate(
            by_exercise.get(planned_exercise.workout_exercise_id, []), start=1
        ):
            db.add(
                SessionSet(
                    session_exercise_id=session_exercise.session_exercise_id,
                    set_order=set_position,
                    # Nothing performed yet; the user fills these in as they go.
                    completed=False,
                    target_reps=target.target_reps,
                    target_weight_kg=target.target_weight_kg,
                    # Copied like the other two. Without these a planned Plank
                    # arrives with nothing to aim at, and performance for a
                    # timed exercise has no denominator.
                    target_duration_seconds=target.target_duration_seconds,
                    target_distance_m=target.target_distance_m,
                    rest_seconds=target.rest_seconds,
                    workout_exercise_set_id=target.workout_exercise_set_id,
                )
            )


@router.get("/workout-sessions", response_model=Page[WorkoutSessionListItem])
def list_workout_sessions(
    current_user: CurrentUserDep,
    db: DbSession,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    on_date: date | None = Query(None, alias="date"),
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
) -> Page[WorkoutSessionListItem]:
    """The caller's sessions, newest first, with totals but without their sets.

    The date filters are what the calendar uses: `?date=` for the one day a
    screen is showing, `?from=&to=` to mark a whole month at once.
    """
    conditions = [WorkoutSession.user_id == current_user.id]
    if status_filter:
        conditions.append(WorkoutSession.status == status_filter)
    if on_date:
        conditions.append(WorkoutSession.scheduled_date == on_date)
    if from_date:
        conditions.append(WorkoutSession.scheduled_date >= from_date)
    if to_date:
        conditions.append(WorkoutSession.scheduled_date <= to_date)

    total = db.scalar(
        select(func.count()).select_from(WorkoutSession).where(*conditions)
    )
    sessions = db.scalars(
        select(WorkoutSession)
        .where(*conditions)
        .order_by(WorkoutSession.started_at.desc(), WorkoutSession.session_id.desc())
        .limit(limit)
        .offset((page - 1) * limit)
    ).all()

    return Page[WorkoutSessionListItem](
        items=build_list_items(db, sessions), page=page, limit=limit, total=total or 0
    )


@router.get("/workout-sessions/{session_id}", response_model=WorkoutSessionDetail)
def get_workout_session(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSessionDetail:
    session = get_owned_session(db, session_id, current_user.id)
    return build_detail(db, session)


@router.post(
    "/workout-sessions/{session_id}/exercises",
    response_model=SessionExerciseRead,
    status_code=status.HTTP_201_CREATED,
)
def add_session_exercise(
    session_id: int,
    payload: SessionExerciseCreate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> SessionExerciseRead:
    """Record an exercise performed in this session, optionally with its sets."""
    session = get_owned_session(db, session_id, current_user.id)
    _reject_if_finished(session)

    if db.get(Exercise, payload.exercise_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Exercise not found"
        )

    if payload.exercise_order is None:
        lock_parent(db, WorkoutSession.session_id, session_id)
        exercise_order = next_order(
            db,
            SessionExercise.exercise_order,
            SessionExercise.session_id == session_id,
        )
    else:
        exercise_order = payload.exercise_order

    names = _exercise_names(db, [payload.exercise_id], language)
    session_exercise = SessionExercise(
        session_id=session_id,
        exercise_id=payload.exercise_id,
        # Snapshotted now: correcting the catalog later must not rewrite this.
        exercise_name=names.get(payload.exercise_id),
        exercise_order=exercise_order,
        notes=payload.notes,
    )
    db.add(session_exercise)
    db.flush()

    for position, performed in enumerate(payload.sets or [], start=1):
        db.add(
            SessionSet(
                session_exercise_id=session_exercise.session_exercise_id,
                set_order=performed.set_order or position,
                reps=performed.reps,
                weight_kg=performed.weight_kg,
                duration_seconds=performed.duration_seconds,
                rest_seconds=performed.rest_seconds,
                completed=performed.completed,
                target_reps=performed.target_reps,
                target_weight_kg=performed.target_weight_kg,
                workout_exercise_set_id=performed.workout_exercise_set_id,
                notes=performed.notes,
            )
        )

    db.commit()
    db.refresh(session_exercise)
    return build_exercise_reads(db, [session_exercise])[0]


@router.post(
    "/workout-sessions/{session_id}/finish", response_model=WorkoutSessionDetail
)
def finish_workout_session(
    session_id: int,
    payload: WorkoutSessionFinish,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSessionDetail:
    """Close the session and stop the clock.

    Duration is not written anywhere - it is the span from `started_at` less
    whatever was paused, computed on read, so it can never disagree with the
    times it comes from.
    """
    session = get_owned_session(db, session_id, current_user.id)

    if session.status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is already {session.status}",
        )

    completed_at = payload.completed_at or datetime.now(timezone.utc)
    if completed_at < session.started_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="completed_at cannot be before started_at",
        )

    # Finishing while paused: the clock stopped when the pause began, so bank
    # that stretch before closing rather than counting the break as training.
    # The DB also requires paused_at to be null for any non-paused row.
    if session.paused_at is not None:
        session.paused_seconds += int(
            (completed_at - session.paused_at).total_seconds()
        )
        session.paused_at = None

    if payload.abandoned:
        # Kept, not deleted: "I started and gave up" is information, and the
        # status column exists precisely so it need not be thrown away. The
        # database requires completed_at to be null for any non-completed row.
        session.status = "abandoned"
        session.completed_at = None
    else:
        session.status = COMPLETED
        session.completed_at = completed_at

    if payload.notes is not None:
        session.notes = payload.notes

    # Phase 4. Only a completed session is evidence of anything - abandoning is
    # the user saying it did not happen, and a record set during an abandoned
    # attempt would be a record they never claimed.
    #
    # Both calls are safe to repeat, which matters because reopening lets the
    # same session be finished more than once: a record has to beat the
    # standing best, and an unlock collides with uq_user_achievements. They
    # share this transaction so a badge cannot survive a rolled-back finish.
    if session.status == COMPLETED:
        # Records first: "your first personal record" is an achievement, and
        # it must be able to see the record this very session just set.
        record_personal_bests(db, current_user.id, session.session_id)
        db.flush()
        evaluate_achievements(db, current_user.id)

    db.commit()
    db.refresh(session)
    return build_detail(db, session)


@router.get("/workout-history", response_model=WorkoutHistoryResponse)
def get_workout_history(
    current_user: CurrentUserDep,
    db: DbSession,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
    limit: int = Query(50, ge=1, le=200),
) -> WorkoutHistoryResponse:
    """Completed sessions over a period, with the totals a summary card wants.

    Only completed sessions: an abandoned or in-progress one is not something
    that happened yet, and averaging it into a month's volume would understate
    every number on the screen.
    """
    if from_date and to_date and to_date < from_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'to' must not be earlier than 'from'",
        )

    conditions = [
        WorkoutSession.user_id == current_user.id,
        WorkoutSession.status == COMPLETED,
    ]
    if from_date:
        conditions.append(WorkoutSession.scheduled_date >= from_date)
    if to_date:
        conditions.append(WorkoutSession.scheduled_date <= to_date)

    sessions = db.scalars(
        select(WorkoutSession)
        .where(*conditions)
        .order_by(
            WorkoutSession.scheduled_date.desc().nulls_last(),
            WorkoutSession.started_at.desc(),
        )
        .limit(limit)
    ).all()

    items = build_list_items(db, sessions)

    return WorkoutHistoryResponse(
        from_date=from_date,
        to_date=to_date,
        totals=HistoryTotals(
            session_count=len(items),
            total_duration_seconds=sum(i.duration_seconds or 0 for i in items),
            total_sets=sum(i.summary.set_count for i in items),
            total_reps=sum(i.summary.total_reps for i in items),
            total_volume_kg=sum(
                (i.summary.total_volume_kg for i in items), start=Decimal("0")
            ),
        ),
        items=items,
    )


@router.delete(
    "/workout-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_workout_session(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Discard a session entirely, with every exercise and set in it.

    Permanent, and there is no soft delete to fall back on. This is for a
    session started by mistake; a workout that was genuinely started and given
    up on is better finished with `abandoned: true`, which keeps the record.
    """
    session = get_owned_session(db, session_id, current_user.id)
    db.delete(session)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/workout-sessions/{session_id}/pause", response_model=WorkoutSessionDetail
)
def pause_workout_session(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSessionDetail:
    """Stop the clock without ending the workout.

    A session is not one continuous block - people take a call, change gyms,
    eat - and counting that as training makes every recorded duration a lie.
    The session stays the user's current workout while paused, so no second
    one can be started, and sets can still be corrected.
    """
    session = get_owned_session(db, session_id, current_user.id)

    if session.status == PAUSED:
        # Idempotent: two taps on a slow connection must not double-count the
        # break or, worse, reset when it started.
        return build_detail(db, session)

    if session.status != IN_PROGRESS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {session.status} and cannot be paused",
        )

    session.status = PAUSED
    session.paused_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(session)
    return build_detail(db, session)


@router.post(
    "/workout-sessions/{session_id}/resume", response_model=WorkoutSessionDetail
)
def resume_workout_session(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSessionDetail:
    """Start the clock again, banking however long the break lasted."""
    session = get_owned_session(db, session_id, current_user.id)

    if session.status == IN_PROGRESS:
        return build_detail(db, session)

    if session.status != PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session is {session.status} and cannot be resumed",
        )

    if session.paused_at is not None:
        session.paused_seconds += int(
            (datetime.now(timezone.utc) - session.paused_at).total_seconds()
        )
    session.status = IN_PROGRESS
    session.paused_at = None

    db.commit()
    db.refresh(session)
    return build_detail(db, session)


@router.post(
    "/workout-sessions/{session_id}/reopen", response_model=WorkoutSessionDetail
)
def reopen_workout_session(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> WorkoutSessionDetail:
    """Pick a finished session back up and keep training.

    "Finish" is the user saying they are done, not the database deciding they
    are: people rack the bar, hit the button and then do one more set. Making
    that impossible pushes the set into a second session that reads, in the
    history, as a second workout on the same afternoon.

    The stretch between finishing and reopening is banked into
    `paused_seconds`, exactly like a pause: whatever the user was doing
    between the two taps, it was not this workout, and counting it would make
    every reopened session's duration a lie. An abandoned session has no
    `completed_at`, so the moment it stopped being active is taken from
    `updated_at`, which the row's trigger stamped then.

    Idempotent for a session that is already active, so a double tap on a slow
    connection cannot bank the gap twice.
    """
    session = get_owned_session(db, session_id, current_user.id)

    if session.status in ACTIVE_STATUSES:
        return build_detail(db, session)

    # One workout at a time is a partial unique index, not a preference: two
    # active rows cannot both exist, so say which one is in the way rather
    # than letting the insert fail with a constraint name.
    clash = (
        db.query(WorkoutSession)
        .filter(
            WorkoutSession.user_id == current_user.id,
            WorkoutSession.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Another workout is already in progress (session {clash.session_id}); "
                "finish it before reopening this one."
            ),
        )

    ended_at = session.completed_at or session.updated_at
    gap = int((datetime.now(timezone.utc) - ended_at).total_seconds())
    if gap > 0:
        session.paused_seconds += gap

    session.status = IN_PROGRESS
    # The CHECK ties `completed_at is not null` to the completed status, so it
    # has to go the moment the status does.
    session.completed_at = None
    session.paused_at = None

    db.commit()
    db.refresh(session)
    return build_detail(db, session)


@router.post(
    "/workout-sessions/{session_id}/sync-plan", response_model=WorkoutSessionDetail
)
def sync_session_with_plan(
    session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> WorkoutSessionDetail:
    """Make the session's exercise list match the plan it came from.

    Starting a workout copies the day in, and the copy is what the session
    records. That is right for history and wrong the moment the user edits the
    day and comes back: they swapped an exercise in the calendar and the open
    session still only offered the old one, with no way out of it.

    So the list is re-synced when the workout is opened:

      * planned exercises missing from the session are added, with their
        target sets, exactly as `POST /workout-sessions` would have copied them
      * exercises in the session that are no longer in the plan are removed,
        **including ones with sets already logged** - dropping an exercise
        from today's plan is the user saying they are not doing it, and
        leaving a half-finished orphan behind contradicts that

    What it deliberately does not do is re-copy an exercise that is still
    planned. Those keep their logged sets: nothing about editing a *different*
    row means the work already done should be thrown away.

    A session with no `workout_day_id` has no plan to sync with and is
    returned untouched.
    """
    session = get_owned_session(db, session_id, current_user.id)

    if session.workout_day_id is None:
        return build_detail(db, session)

    day = db.get(WorkoutDay, session.workout_day_id)
    if day is None:
        # The plan was deleted; the session keeps what it recorded.
        return build_detail(db, session)

    planned = db.scalars(
        select(WorkoutExercise)
        .where(WorkoutExercise.workout_day_id == day.workout_day_id)
        .order_by(WorkoutExercise.exercise_order)
    ).all()
    planned_ids = [we.exercise_id for we in planned]

    existing = db.scalars(
        select(SessionExercise)
        .where(SessionExercise.session_id == session.session_id)
        .order_by(SessionExercise.exercise_order)
    ).all()
    existing_by_exercise = {se.exercise_id: se for se in existing}

    for session_exercise in existing:
        if session_exercise.exercise_id not in planned_ids:
            db.delete(session_exercise)

    names = _exercise_names(db, planned_ids, language)

    target_sets = db.scalars(
        select(WorkoutExerciseSet)
        .where(
            WorkoutExerciseSet.workout_exercise_id.in_(
                [we.workout_exercise_id for we in planned]
            )
        )
        .order_by(WorkoutExerciseSet.workout_exercise_id, WorkoutExerciseSet.set_order)
    ).all()
    by_exercise: dict[int, list[WorkoutExerciseSet]] = {}
    for target in target_sets:
        by_exercise.setdefault(target.workout_exercise_id, []).append(target)

    # Renumbered from the plan so the session reads in the order the calendar
    # shows, however the rows got there.
    for position, planned_exercise in enumerate(planned, start=1):
        session_exercise = existing_by_exercise.get(planned_exercise.exercise_id)

        if session_exercise is not None:
            session_exercise.exercise_order = position
            continue

        session_exercise = SessionExercise(
            session_id=session.session_id,
            exercise_id=planned_exercise.exercise_id,
            exercise_name=names.get(planned_exercise.exercise_id),
            exercise_order=position,
            notes=planned_exercise.notes,
        )
        db.add(session_exercise)
        db.flush()

        for set_position, target in enumerate(
            by_exercise.get(planned_exercise.workout_exercise_id, []), start=1
        ):
            db.add(
                SessionSet(
                    session_exercise_id=session_exercise.session_exercise_id,
                    set_order=set_position,
                    completed=False,
                    target_reps=target.target_reps,
                    target_weight_kg=target.target_weight_kg,
                    target_duration_seconds=target.target_duration_seconds,
                    target_distance_m=target.target_distance_m,
                    rest_seconds=target.rest_seconds,
                    workout_exercise_set_id=target.workout_exercise_set_id,
                )
            )

    db.commit()
    db.refresh(session)
    return build_detail(db, session)
