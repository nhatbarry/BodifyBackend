"""Seed a plausible training history so the progress screens have something to show.

Phase 4 draws a heatmap, a set of progression charts, a personal-record list and
nine achievements - all of which read as broken on a fresh account, because they
are correct and empty at the same time. This fills in months of workouts so the
screens can be judged on how they look rather than on how they degrade.

What it writes
--------------
A complete plan and the sessions performed from it - the same two halves a real
user leaves behind:

    workout_plans -> workout_days -> workout_exercises -> workout_exercise_sets
                          |
                     schedules (one row per date, which is what puts a day on
                                the calendar at all)
                          |
    workout_sessions -> session_exercises -> session_sets

Sessions link back to the day they were performed from, and every seeded
`session_sets` row points at the `workout_exercise_sets` row it was copied from.
That link is the whole reason the plan side exists: without a schedule row the
calendar shows nothing on a date, and without matching plan rows a past date has
no "KH" to print the "TT" against - the history exists but the calendar looks
like the user never trained.

Everything goes into **one dedicated plan per account**, tagged in its
description. The account's own plans are never edited or deleted. The one thing
the seeder does touch is which plan is active, because only one can be, and it
reports what it displaced.

Records and achievements are not written by hand. Each session is replayed
through `record_personal_bests()` and `evaluate()`, the same two calls
`POST /workout-sessions/{id}/finish` makes, so the numbers are consistent with
the ones a real workout would have produced. Both services stamp `achieved_at`
with `now()`; this backdates it to the session that earned it, otherwise six
months of records land on today and the record list reads as one pile.

Shape of the data
-----------------
Session sizes are deliberately uneven. The heatmap shades each day against the
busiest day in the window, so a routine of exactly eighteen sets every time
would render as one flat colour on every square - technically accurate and
completely unreadable. The mix of quick, normal and long sessions below is what
produces all four shades.

Undo
----
Seeded sessions carry `notes = '[seed]'` and the seeded plan carries the tag in
its description. `--undo` removes both, plus the records traced to their sets,
rebuilds `personal_bests` from whatever history survives, and re-evaluates
achievements from scratch. Achievements are pure functions of the record, so
re-evaluating restores exactly the set the remaining real data supports - the
one cost is that a genuinely earned badge gets a fresh `achieved_at`.

Usage
-----
    python scripts/seed_fake_history.py --list
    python scripts/seed_fake_history.py --email a@b.com --email c@d.com
    python scripts/seed_fake_history.py --email a@b.com --weeks 26 --per-week 3
    python scripts/seed_fake_history.py --email a@b.com --dry-run
    python scripts/seed_fake_history.py --email a@b.com --undo
"""

from __future__ import annotations

import argparse
import random
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.progress import (  # noqa: E402
    PersonalBest,
    PersonalRecord,
    UserAchievement,
)
from app.models.schedules import Schedule  # noqa: E402
from app.models.session_exercises import SessionExercise  # noqa: E402
from app.models.session_sets import SessionSet  # noqa: E402
from app.models.workout_days import WorkoutDay  # noqa: E402
from app.models.workout_exercise_sets import WorkoutExerciseSet  # noqa: E402
from app.models.workout_exercises import WorkoutExercise  # noqa: E402
from app.models.workout_plans import WorkoutPlan  # noqa: E402
from app.models.workout_sessions import COMPLETED, WorkoutSession  # noqa: E402
from app.services.achievements import evaluate  # noqa: E402
from app.services.personal_records import record_personal_bests  # noqa: E402

SEED_TAG = "[seed]"
PLAN_NAME = "Push Pull Legs"

# Sessions are stamped at 11:00 UTC - early evening in Vietnam, and safely
# mid-day in UTC too. `recent_activity` groups on `date(completed_at)`, so a
# time near midnight in either zone would scatter workouts onto the wrong day.
TRAINING_HOUR = time(11, 0)

# Gaps this long between two workouts get marked as rest days on the calendar.
# Longer ones are left unscheduled: a fortnight off is not a rest week, an
# absent date reads correctly as "nothing planned", and the app's own rule
# refuses more than three rest days in a row.
MAX_REST_GAP = 3


@dataclass(frozen=True)
class Lift:
    """One movement in a split, with the load it starts at and how it climbs.

    `gain_per_month` is what makes the progression charts and the record list
    worth looking at: a history at a constant weight sets one personal best on
    day one and then never moves again.
    """

    exercise_id: int
    sets: int
    reps: int | None = None
    start_weight: float | None = None
    gain_per_month: float = 0.0
    # Time-based and distance-based work, so the two minority tracking types
    # are represented rather than only ever tested by hand.
    start_duration: int | None = None
    duration_gain_per_month: float = 0.0
    distance_m: float | None = None


@dataclass(frozen=True)
class Target:
    """What the plan asked for on one set."""

    reps: int | None = None
    weight: Decimal | None = None
    duration: int | None = None
    distance: Decimal | None = None


# Exercise ids are real rows from the catalog, so names, thumbnails and
# `tracking_type` all resolve on every screen.
SPLITS: dict[str, list[Lift]] = {
    "Đẩy (Push)": [
        Lift(208, sets=4, reps=8, start_weight=55, gain_per_month=2.5),   # Bench Press
        Lift(210, sets=3, reps=10, start_weight=35, gain_per_month=2.0),  # Incline Bench
        Lift(548, sets=3, reps=8, start_weight=32.5, gain_per_month=1.5), # Military Press
        Lift(565, sets=3, reps=14, start_weight=7.5, gain_per_month=0.5), # Cable Lateral Raise
        # Fifth, not sixth: the two minority tracking types have to appear in
        # ordinary sessions, or they only ever land in the rare long one.
        Lift(8, sets=3, start_duration=50, duration_gain_per_month=15),   # Plank
        Lift(212, sets=3, reps=10, start_weight=0, gain_per_month=1.0),   # Chest Dip
    ],
    "Kéo (Pull)": [
        Lift(323, sets=4, reps=5, start_weight=80, gain_per_month=5.0),   # Deadlift
        Lift(439, sets=3, reps=10, start_weight=45, gain_per_month=2.5),  # Lat Pulldown
        Lift(480, sets=3, reps=10, start_weight=40, gain_per_month=2.5),  # Cable Row
        Lift(49, sets=3, reps=12, start_weight=22.5, gain_per_month=1.0), # Barbell Curl
        Lift(552, sets=3, reps=15, start_weight=15, gain_per_month=1.0),  # Face Pull
        Lift(402, sets=3, reps=8, start_weight=0, gain_per_month=1.0),    # Pull Up
    ],
    "Chân (Legs)": [
        Lift(511, sets=4, reps=6, start_weight=70, gain_per_month=4.0),   # Back Squat
        Lift(324, sets=3, reps=8, start_weight=60, gain_per_month=3.0),   # Romanian Deadlift
        Lift(501, sets=3, reps=12, start_weight=120, gain_per_month=7.5), # Leg Press
        Lift(325, sets=3, reps=12, start_weight=35, gain_per_month=2.0),  # Leg Curl
        Lift(250, sets=3, distance_m=20, start_weight=22, gain_per_month=2.0),  # Farmers Carry
        Lift(200, sets=4, reps=15, start_weight=30, gain_per_month=2.5),  # Calf Raise
    ],
}

ROTATION = list(SPLITS)

# How many of a split's movements get done, and how hard. The heatmap shades
# against the busiest day in its window, so this spread is the difference
# between four legible shades and one flat block of colour.
SESSION_SIZES = (
    ("quick", 3, -1, 0.10),   # squeezed in - about 6 sets
    ("short", 4, 0, 0.20),    # ~10 sets
    ("normal", 5, 0, 0.45),   # ~16 sets
    ("long", 6, 1, 0.25),     # ~22 sets
)

SESSION_MINUTES = {
    "quick": (22, 32),
    "short": (35, 48),
    "normal": (48, 68),
    "long": (65, 88),
}


def resolve_users(db: Session, emails: list[str]) -> list[tuple[str, uuid.UUID]]:
    """Map addresses to ids, refusing the whole run if any is unknown.

    Seeding half the accounts asked for and reporting the rest as a warning is
    the kind of partial success that gets missed in a scroll-back.
    """
    found: list[tuple[str, uuid.UUID]] = []
    missing: list[str] = []

    for email in emails:
        row = db.execute(
            text("select id from auth.users where lower(email) = lower(:e)"),
            {"e": email},
        ).first()
        if row is None:
            missing.append(email)
        else:
            found.append((email, row[0]))

    if missing:
        raise SystemExit(
            "Unknown account(s): "
            + ", ".join(missing)
            + "\nRun with --list to see what exists."
        )
    return found


def exercise_names(db: Session, ids: set[int]) -> dict[int, str]:
    """Vietnamese catalog names, falling back to English then to the id.

    Snapshotted into `session_exercises.exercise_name` the same way a real
    session does, so a later catalog edit cannot rewrite this history either.
    """
    rows = db.execute(
        text(
            """
            select e.exercise_id,
                   max(case when t.language = 'vi' then t.name end) as vi,
                   max(case when t.language = 'en' then t.name end) as en
            from exercises e
            left join exercise_translations t on t.exercise_id = e.exercise_id
            where e.exercise_id = any(:ids)
            group by e.exercise_id
            """
        ),
        {"ids": list(ids)},
    ).all()

    names = {r.exercise_id: (r.vi or r.en or f"#{r.exercise_id}") for r in rows}

    unknown = ids - set(names)
    if unknown:
        raise SystemExit(
            f"Exercise id(s) not in the catalog: {sorted(unknown)}. "
            "The split definitions in this script are out of date."
        )
    return names


def training_dates(rng: random.Random, weeks: int, per_week: int) -> list[date]:
    """The days trained, oldest first.

    Mondays, Wednesdays and Fridays would be the honest reading of "three days
    a week", and would also draw three perfectly straight lines across the
    heatmap. Real attendance drifts, so each day is allowed to slip by one and
    roughly one week in ten is missed altogether.
    """
    today = date.today()
    # Start on the Monday `weeks` weeks back, so the grid's leftmost column is
    # a full one.
    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))

    base = [0, 2, 4][:per_week] or [0]
    if per_week > 3:
        base = sorted(rng.sample(range(7), per_week))

    # One week trained straight through. Without it the seven-day streak badge
    # can never fire on a three-day routine, and an achievement that no amount
    # of seeded history can reach is not a badge, it is a bug report waiting to
    # be filed. People do run challenge weeks.
    streak_week = (
        rng.randrange(weeks // 3, max(weeks // 3 + 1, weeks - 2)) if weeks >= 9 else None
    )

    days: list[date] = []
    for week in range(weeks):
        monday = start + timedelta(days=7 * week)

        if week == streak_week:
            days.extend(
                monday + timedelta(days=offset)
                for offset in range(7)
                if start <= monday + timedelta(days=offset) <= today
            )
            continue

        if rng.random() < 0.09:
            continue  # a week off - illness, travel, life
        for offset in base:
            day = monday + timedelta(days=offset)
            if rng.random() < 0.25:
                day += timedelta(days=rng.choice((-1, 1)))
            if start <= day <= today:
                days.append(day)

    return sorted(set(days))


def already_trained(db: Session, user_id: uuid.UUID) -> set[date]:
    """Dates the account already has a session on.

    Seeding onto one of them puts two sessions on a single day, and the
    calendar asks for that date with `limit=1` ordered by `started_at DESC` -
    so it gets whichever ran later and, if that is the real one, finds none of
    the seeded plan's exercises in it and prints "Chưa tập" over a workout that
    is right there in the history. Skipping the date is the only fix that does
    not involve touching the user's own record.
    """
    rows = db.execute(
        select(
            func.coalesce(
                WorkoutSession.scheduled_date, func.date(WorkoutSession.started_at)
            )
        ).where(WorkoutSession.user_id == user_id)
    ).scalars().all()
    return {row for row in rows if row is not None}


def rest_dates(trained: list[date]) -> list[date]:
    """Days between workouts, marked as rest rather than left blank.

    A rest day is a row, not an absence - the calendar has to be able to tell
    "resting today" from "nothing planned yet". Only short gaps qualify, so a
    skipped fortnight stays unscheduled instead of being dressed up as a plan.
    """
    out: list[date] = []
    for earlier, later in zip(trained, trained[1:]):
        gap = (later - earlier).days - 1
        if 1 <= gap <= MAX_REST_GAP:
            out.extend(earlier + timedelta(days=n) for n in range(1, gap + 1))
    return out


def pick_size(rng: random.Random) -> tuple[str, int, int]:
    roll = rng.random()
    cumulative = 0.0
    for label, count, set_delta, weight in SESSION_SIZES:
        cumulative += weight
        if roll <= cumulative:
            return label, count, set_delta
    label, count, set_delta, _ = SESSION_SIZES[-1]
    return label, count, set_delta


def round_plate(weight: float) -> Decimal:
    """To the nearest 2.5 kg - the smallest change a loaded bar can express."""
    return Decimal(str(round(weight / 2.5) * 2.5)).quantize(Decimal("0.01"))


def plan_target(lift: Lift, set_order: int, set_count: int, months_in: float) -> Target:
    """What the plan asks for on this set, at this point in the programme."""
    if lift.start_duration is not None:
        seconds = int(lift.start_duration + lift.duration_gain_per_month * months_in)
        # Holds fall off across the sets - the third plank is never the first.
        return Target(duration=max(15, seconds - 5 * (set_order - 1)))

    weight = round_plate((lift.start_weight or 0) + lift.gain_per_month * months_in)

    if lift.distance_m is not None:
        return Target(weight=weight, distance=Decimal(str(lift.distance_m)))

    # The last set is planned one rep short, the way most programmes taper.
    reps = max(1, (lift.reps or 8) - (1 if set_order == set_count else 0))
    return Target(reps=reps, weight=weight)


def performed(target: Target, rng: random.Random) -> dict:
    """What actually happened against that target.

    Mostly the plan is met, sometimes it is missed by a rep, occasionally
    beaten - which is what stops performance % sitting on exactly 100 for six
    months, a number nobody would believe was being computed.
    """
    if target.duration is not None:
        return {"duration_seconds": max(5, target.duration + rng.randint(-4, 6))}

    if target.distance is not None:
        return {
            "distance_m": target.distance
            + Decimal(rng.choice(("-2", "0", "0", "0", "3"))),
            "weight_kg": target.weight,
        }

    reps = target.reps or 0
    roll = rng.random()
    if roll < 0.18:
        reps = max(1, reps - rng.randint(1, 2))
    elif roll < 0.30:
        reps += 1

    weight = target.weight
    if weight is not None and rng.random() < 0.08:
        weight = max(Decimal("0"), weight - Decimal("2.5"))

    return {"reps": reps, "weight_kg": weight}


def create_plan(db: Session, user_id: uuid.UUID, first: date, weeks: int) -> WorkoutPlan:
    """The seeded plan. One per account, tagged so `--undo` can find it.

    `start_date` and `duration_weeks` are both set because a plan without them
    has no window and can therefore never be "completed" - which would leave
    the ninth achievement permanently out of reach no matter what was seeded.
    """
    plan = WorkoutPlan(
        user_id=user_id,
        name=PLAN_NAME,
        description=f"{SEED_TAG} generated training history",
        goal="strength",
        duration_weeks=weeks,
        start_date=first,
        status="active",
        is_active=False,
    )
    db.add(plan)
    db.flush()
    return plan


def build_day(
    db: Session,
    plan: WorkoutPlan,
    user_id: uuid.UUID,
    on_date: date,
    day_order: int,
    split: str | None,
    months_in: float,
    rng: random.Random,
) -> tuple[WorkoutDay, list[tuple[Lift, WorkoutExercise, list[WorkoutExerciseSet]]]]:
    """A day in the plan and its schedule row, always together.

    A day without its schedule is invisible on the calendar and unreachable by
    date - exactly the gap that made the first version of this script produce a
    history the calendar could not show.
    """
    day = WorkoutDay(
        plan_id=plan.plan_id,
        name=split or on_date.isoformat(),
        day_order=day_order,
        is_rest_day=split is None,
    )
    db.add(day)
    db.flush()
    db.add(
        Schedule(
            user_id=user_id,
            workout_day_id=day.workout_day_id,
            scheduled_date=on_date,
        )
    )

    if split is None:
        return day, []

    _, exercise_count, set_delta = pick_size(rng)
    planned: list[tuple[Lift, WorkoutExercise, list[WorkoutExerciseSet]]] = []

    for order, lift in enumerate(SPLITS[split][:exercise_count], start=1):
        exercise = WorkoutExercise(
            workout_day_id=day.workout_day_id,
            exercise_id=lift.exercise_id,
            exercise_order=order,
        )
        db.add(exercise)
        db.flush()

        set_count = max(2, lift.sets + set_delta)
        rows: list[WorkoutExerciseSet] = []
        for set_order in range(1, set_count + 1):
            target = plan_target(lift, set_order, set_count, months_in)
            row = WorkoutExerciseSet(
                workout_exercise_id=exercise.workout_exercise_id,
                set_order=set_order,
                target_reps=target.reps,
                target_weight_kg=target.weight,
                target_duration_seconds=target.duration,
                target_distance_m=target.distance,
                rest_seconds=rng.choice((60, 90, 90, 120, 150)),
            )
            db.add(row)
            rows.append(row)

        db.flush()
        planned.append((lift, exercise, rows))

    return day, planned


def build_session(
    db: Session,
    user_id: uuid.UUID,
    plan: WorkoutPlan,
    day: WorkoutDay,
    planned: list[tuple[Lift, WorkoutExercise, list[WorkoutExerciseSet]]],
    on_date: date,
    names: dict[int, str],
    rng: random.Random,
) -> WorkoutSession:
    """The workout performed from that day, copied the way a real start copies.

    Every set keeps `workout_exercise_set_id` and both target columns, which is
    what lets the calendar print "TT 12 / 10" underneath "KH 10 / 10 / 8"
    instead of showing a planned day nobody appears to have trained.
    """
    total_sets = sum(len(rows) for _, _, rows in planned)
    label = (
        "quick" if total_sets <= 8
        else "short" if total_sets <= 12
        else "normal" if total_sets <= 18
        else "long"
    )

    started = datetime.combine(on_date, TRAINING_HOUR, tzinfo=timezone.utc)
    started += timedelta(minutes=rng.randint(-40, 40))
    duration = timedelta(minutes=rng.randint(*SESSION_MINUTES[label]))
    # A break long enough to be worth banking happens now and then; the clock
    # subtracts it, which is the whole point of `paused_seconds`.
    paused = rng.choice((0, 0, 0, 0, rng.randint(90, 420)))

    session = WorkoutSession(
        user_id=user_id,
        workout_day_id=day.workout_day_id,
        plan_name=plan.name,
        day_name=day.name,
        scheduled_date=on_date,
        started_at=started,
        completed_at=started + duration,
        paused_at=None,
        paused_seconds=paused,
        status=COMPLETED,
        notes=SEED_TAG,
        created_at=started,
        updated_at=started + duration,
    )
    db.add(session)
    db.flush()

    for order, (lift, _, rows) in enumerate(planned, start=1):
        exercise = SessionExercise(
            session_id=session.session_id,
            exercise_id=lift.exercise_id,
            exercise_name=names[lift.exercise_id],
            exercise_order=order,
            created_at=started,
            updated_at=started,
        )
        db.add(exercise)
        db.flush()

        for planned_set in rows:
            actual = performed(
                Target(
                    reps=planned_set.target_reps,
                    weight=planned_set.target_weight_kg,
                    duration=planned_set.target_duration_seconds,
                    distance=planned_set.target_distance_m,
                ),
                rng,
            )
            db.add(
                SessionSet(
                    session_exercise_id=exercise.session_exercise_id,
                    set_order=planned_set.set_order,
                    rest_seconds=planned_set.rest_seconds,
                    # The odd set gets abandoned. Completion % pinned at 100
                    # for six months reads as a number nobody is computing.
                    completed=rng.random() > 0.03,
                    target_reps=planned_set.target_reps,
                    target_weight_kg=planned_set.target_weight_kg,
                    target_duration_seconds=planned_set.target_duration_seconds,
                    target_distance_m=planned_set.target_distance_m,
                    workout_exercise_set_id=planned_set.workout_exercise_set_id,
                    created_at=started,
                    updated_at=started,
                    **actual,
                )
            )

    return session


def backdate(
    db: Session, user_id: uuid.UUID, records, achievements, when: datetime
) -> None:
    """Move the stamps the services wrote with `now()` back onto the session.

    `record_personal_bests` and `evaluate` both date their rows to the moment
    they run, which is right for a live finish and wrong for a replay: without
    this, half a year of records and every badge land on today, and the record
    list becomes one undated pile.
    """
    for record in records:
        record.achieved_at = when
        record.created_at = when

    if records:
        db.execute(
            update(PersonalBest)
            .where(
                PersonalBest.user_id == user_id,
                PersonalBest.exercise_id.in_({r.exercise_id for r in records}),
                PersonalBest.record_type.in_({r.record_type for r in records}),
            )
            .values(achieved_at=when, updated_at=when)
        )

    if achievements:
        db.execute(
            update(UserAchievement)
            .where(
                UserAchievement.user_id == user_id,
                UserAchievement.achievement_id.in_(
                    {a.achievement_id for a in achievements}
                ),
            )
            .values(achieved_at=when)
        )


def activate(db: Session, user_id: uuid.UUID, plan_id: int) -> str | None:
    """Make the seeded plan the active one, returning what it displaced.

    Exactly one plan per user may be active - a partial unique index, not a
    convention - so the old one has to be cleared first, in its own statement.
    Reported rather than done quietly: which plan is active is the user's
    choice, and the seeder is overruling it.
    """
    previous = db.execute(
        select(WorkoutPlan.name).where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
            WorkoutPlan.plan_id != plan_id,
        )
    ).scalar_one_or_none()

    db.execute(
        update(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
        .values(is_active=False)
    )
    db.flush()
    db.execute(
        update(WorkoutPlan).where(WorkoutPlan.plan_id == plan_id).values(is_active=True)
    )
    return previous


def seed_account(
    db: Session,
    email: str,
    user_id: uuid.UUID,
    weeks: int,
    per_week: int,
    rng: random.Random,
    names: dict[int, str],
) -> None:
    taken = already_trained(db, user_id)
    trained = [day for day in training_dates(rng, weeks, per_week) if day not in taken]
    if not trained:
        print(f"  {email}: nothing to seed")
        return

    plan = create_plan(db, user_id, trained[0], weeks)

    # Rest days and training days share one ordering, so `day_order` runs in
    # calendar order rather than as two interleaved sequences.
    schedule: list[tuple[date, str | None]] = [
        (day, ROTATION[index % len(ROTATION)]) for index, day in enumerate(trained)
    ]
    schedule += [(day, None) for day in rest_dates(trained)]
    schedule.sort(key=lambda pair: pair[0])

    first = trained[0]
    records_total = 0
    unlocked_total: list[str] = []

    for order, (on_date, split) in enumerate(schedule, start=1):
        months_in = (on_date - first).days / 30.44
        day, planned = build_day(db, plan, user_id, on_date, order, split, months_in, rng)
        if split is None:
            continue

        session = build_session(db, user_id, plan, day, planned, on_date, names, rng)
        db.flush()

        # The same two calls a real finish makes, in the same order: a record
        # has to exist before "your first personal record" can see it.
        records = record_personal_bests(db, user_id, session.session_id)
        db.flush()
        unlocked = evaluate(db, user_id)
        db.flush()

        backdate(db, user_id, records, unlocked, session.completed_at)

        records_total += len(records)
        unlocked_total.extend(a.code for a in unlocked)

    displaced = activate(db, user_id, plan.plan_id)
    db.flush()

    total_sets = db.execute(
        select(func.count())
        .select_from(SessionSet)
        .join(
            SessionExercise,
            SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
        )
        .join(WorkoutSession, WorkoutSession.session_id == SessionExercise.session_id)
        .where(WorkoutSession.user_id == user_id, WorkoutSession.notes == SEED_TAG)
    ).scalar_one()

    print(
        f"  {email}: {len(trained)} sessions, {total_sets} sets, "
        f"{len(schedule) - len(trained)} rest days, {trained[0]} -> {trained[-1]}, "
        f"{records_total} records, {len(unlocked_total)} achievements"
    )
    if taken:
        print(f"    skipped {len(taken)} date(s) the account had already trained")
    if unlocked_total:
        print(f"    unlocked: {', '.join(unlocked_total)}")
    print(f"    plan '{PLAN_NAME}' is now the active one", end="")
    print(f" (was '{displaced}')" if displaced else " (none was active before)")


def undo_account(db: Session, email: str, user_id: uuid.UUID) -> None:
    """Remove the seeded plan and sessions, and put the derived tables back."""
    session_ids = list(
        db.execute(
            select(WorkoutSession.session_id).where(
                WorkoutSession.user_id == user_id,
                WorkoutSession.notes == SEED_TAG,
            )
        ).scalars()
    )
    plan_ids = list(
        db.execute(
            select(WorkoutPlan.plan_id).where(
                WorkoutPlan.user_id == user_id,
                WorkoutPlan.description.like(f"{SEED_TAG}%"),
            )
        ).scalars()
    )
    if not session_ids and not plan_ids:
        print(f"  {email}: nothing seeded")
        return

    removed_records = 0
    if session_ids:
        set_ids = (
            select(SessionSet.session_set_id)
            .join(
                SessionExercise,
                SessionExercise.session_exercise_id == SessionSet.session_exercise_id,
            )
            .where(SessionExercise.session_id.in_(session_ids))
        )
        # Records first. The FK is SET NULL, so deleting the sessions would
        # leave the record rows behind, orphaned and indistinguishable from
        # real ones.
        removed_records = db.execute(
            delete(PersonalRecord).where(
                PersonalRecord.user_id == user_id,
                PersonalRecord.set_id.in_(set_ids),
            )
        ).rowcount
        db.execute(
            delete(WorkoutSession).where(WorkoutSession.session_id.in_(session_ids))
        )

    if plan_ids:
        # Cascades to days -> exercises -> sets -> schedules.
        db.execute(delete(WorkoutPlan).where(WorkoutPlan.plan_id.in_(plan_ids)))
    db.flush()

    # `personal_bests` is the current best, so it has to be rebuilt from what
    # is left rather than patched.
    db.execute(delete(PersonalBest).where(PersonalBest.user_id == user_id))
    db.execute(
        text(
            """
            insert into personal_bests
                (user_id, exercise_id, record_type, value, unit, session_set_id,
                 achieved_at, created_at, updated_at)
            select distinct on (exercise_id, record_type)
                   user_id, exercise_id, record_type, value, unit, set_id,
                   achieved_at, achieved_at, achieved_at
            from personal_records
            where user_id = :uid
            order by exercise_id, record_type, value desc, achieved_at desc
            """
        ),
        {"uid": user_id},
    )

    # Achievements are a pure function of the record, so the honest way to
    # withdraw the seeded ones is to re-derive the whole set.
    db.execute(delete(UserAchievement).where(UserAchievement.user_id == user_id))
    db.flush()
    restored = evaluate(db, user_id)

    # Deleting the active plan leaves the account with none. Promote whatever
    # the user last touched rather than leaving the Tracker tab with nothing.
    still_active = db.execute(
        select(func.count())
        .select_from(WorkoutPlan)
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
    ).scalar_one()

    promoted = None
    if not still_active:
        promoted = db.execute(
            select(WorkoutPlan.plan_id, WorkoutPlan.name)
            .where(WorkoutPlan.user_id == user_id, WorkoutPlan.status != "archived")
            .order_by(WorkoutPlan.updated_at.desc())
            .limit(1)
        ).first()
        if promoted:
            db.execute(
                update(WorkoutPlan)
                .where(WorkoutPlan.plan_id == promoted[0])
                .values(is_active=True)
            )

    print(
        f"  {email}: removed {len(session_ids)} sessions, {len(plan_ids)} plan(s), "
        f"{removed_records} records; {len(restored)} achievements still earned"
    )
    if promoted:
        print(f"    reactivated '{promoted[1]}'")


def list_accounts(db: Session) -> None:
    rows = db.execute(
        text(
            """
            select u.email, u.created_at::date as joined,
                   (select count(*) from workout_sessions s
                     where s.user_id = u.id) as sessions,
                   (select count(*) from workout_sessions s
                     where s.user_id = u.id and s.notes = :tag) as seeded,
                   (select count(*) from workout_plans p
                     where p.user_id = u.id) as plans
            from auth.users u
            order by u.created_at
            """
        ),
        {"tag": SEED_TAG},
    ).all()

    if not rows:
        print("No accounts.")
        return
    print(f"{'email':40} {'joined':12} {'sessions':>9} {'seeded':>7} {'plans':>6}")
    for r in rows:
        print(f"{r.email:40} {str(r.joined):12} {r.sessions:9} {r.seeded:7} {r.plans:6}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--email",
        action="append",
        default=[],
        metavar="ADDRESS",
        help="account to seed; repeat for several",
    )
    parser.add_argument("--weeks", type=int, default=26,
                        help="how far back to go (default 26, about six months)")
    parser.add_argument("--per-week", type=int, default=3,
                        help="training days per week (default 3)")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed; same seed and account gives the same history")
    parser.add_argument("--undo", action="store_true",
                        help="remove the seeded plan and sessions, and rebuild the "
                             "derived tables")
    parser.add_argument("--list", action="store_true",
                        help="show accounts and what they already hold")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be written, then roll back")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            list_accounts(db)
            return

        if not args.email:
            parser.error("--email is required (or use --list)")
        if args.weeks < 1 or args.per_week < 1 or args.per_week > 7:
            parser.error("--weeks must be >= 1 and --per-week between 1 and 7")

        users = resolve_users(db, args.email)

        if args.undo:
            print(f"Undoing {SEED_TAG} data for {len(users)} account(s):")
            for email, user_id in users:
                undo_account(db, email, user_id)
        else:
            names = exercise_names(
                db, {lift.exercise_id for split in SPLITS.values() for lift in split}
            )
            print(
                f"Seeding {args.weeks} weeks x {args.per_week} days "
                f"for {len(users)} account(s):"
            )
            for email, user_id in users:
                # Seeded per account, so two accounts do not get identical
                # histories and a rerun of one is reproducible.
                rng = random.Random(
                    args.seed if args.seed is not None else hash(str(user_id)) & 0xFFFF
                )
                seed_account(db, email, user_id, args.weeks, args.per_week, rng, names)

        if args.dry_run:
            db.rollback()
            print("\n--dry-run: rolled back, nothing written.")
        else:
            db.commit()
            print("\nCommitted.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
