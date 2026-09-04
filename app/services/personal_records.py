"""Detecting personal records from a session that has just been finished.

Called from `POST /workout-sessions/{id}/finish`, which is the only moment the
numbers are settled. It has to be safe to call more than once: a session can be
reopened and finished again, and the second finish sees exactly the same sets.

Idempotency is not handled by remembering what was already awarded. It falls
out of the rule itself - a record is only a record if it beats the current
best, and the second pass never does. The comparison is done by the database
in one statement so two concurrent finishes cannot both decide they beat the
same 50 kg; see `PersonalBest`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.progress import (
    ESTIMATED_1RM,
    MAX_DISTANCE,
    MAX_DURATION,
    MAX_REPS,
    MAX_VOLUME,
    MAX_WEIGHT,
    PersonalBest,
    PersonalRecord,
)
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet

# The precision the value columns store. Candidates are rounded to it before
# being compared, so a computed value can never beat its own stored form.
_STORED = Decimal("0.01")

# The unit each record is quoted in. Kept beside the value because a chart
# axis needs it and a "45" with no unit is not a record of anything.
UNITS = {
    MAX_WEIGHT: "kg",
    MAX_REPS: "reps",
    MAX_VOLUME: "kg",
    MAX_DURATION: "seconds",
    MAX_DISTANCE: "m",
    ESTIMATED_1RM: "kg",
}


@dataclass(frozen=True)
class Candidate:
    """The best a single session managed for one exercise and record type."""

    exercise_id: int
    record_type: str
    value: Decimal
    session_set_id: int | None


def estimated_1rm(weight: Decimal, reps: int) -> Decimal | None:
    """Epley: 1RM = w x (1 + reps/30).

    A single rep is special-cased. Epley would return w x 1.033 for reps=1,
    which claims the user can lift more than the weight they just proved they
    can lift once - a one-rep max *is* the weight.

    Above roughly ten reps the formula stops meaning much, but it is not
    clamped here: refusing to estimate would leave high-rep sets with no 1RM
    at all, and a slightly generous estimate is the accepted trade in every
    lifting app that shows one.
    """
    if reps < 1:
        return None
    if reps == 1:
        return weight
    return weight * (Decimal(1) + Decimal(reps) / Decimal(30))


def collect_candidates(db: Session, session_id: int) -> list[Candidate]:
    """The best value per (exercise, record_type) in one finished session.

    Only completed sets count. A set left unlogged is not a performance, and a
    row that still holds nothing but its copied-in target must not be read as
    someone lifting the target.

    Which record types apply is decided by the data, not by `tracking_type`: a
    Plank has no weight so it produces no max_weight, a Farmer's Carry has both
    metres and kilos so it produces both. That keeps the rule true for the
    loaded carries and weighted holds, where the tracking type says `distance`
    or `duration` but the load is still real.
    """
    rows = db.execute(
        select(
            SessionExercise.exercise_id,
            SessionSet.session_set_id,
            SessionSet.reps,
            SessionSet.weight_kg,
            SessionSet.duration_seconds,
            SessionSet.distance_m,
        )
        .join(
            SessionSet,
            SessionSet.session_exercise_id == SessionExercise.session_exercise_id,
        )
        .where(
            SessionExercise.session_id == session_id,
            SessionSet.completed.is_(True),
        )
    ).all()

    best: dict[tuple[int, str], Candidate] = {}

    def offer(
        exercise_id: int,
        record_type: str,
        value: Decimal | None,
        set_id: int | None,
    ) -> None:
        if value is None or value <= 0:
            return
        # Rounded to what the column can hold before anything compares it.
        # Epley returns 44.3333... where NUMERIC(10,2) stores 44.33, so an
        # unrounded candidate beat its own stored value on every finish and
        # appended an identical record each time - caught by re-finishing a
        # real session four times and finding four identical 1RM rows.
        value = value.quantize(_STORED)
        key = (exercise_id, record_type)
        current = best.get(key)
        if current is None or value > current.value:
            best[key] = Candidate(exercise_id, record_type, value, set_id)

    for exercise_id, set_id, reps, weight, duration, distance in rows:
        if weight is not None:
            offer(exercise_id, MAX_WEIGHT, weight, set_id)
        if reps is not None:
            offer(exercise_id, MAX_REPS, Decimal(reps), set_id)
        if reps is not None and weight is not None:
            offer(exercise_id, MAX_VOLUME, weight * reps, set_id)
            offer(exercise_id, ESTIMATED_1RM, estimated_1rm(weight, reps), set_id)
        if duration is not None:
            offer(exercise_id, MAX_DURATION, Decimal(duration), set_id)
        if distance is not None:
            offer(exercise_id, MAX_DISTANCE, distance, set_id)

    return list(best.values())


def record_personal_bests(
    db: Session, user_id: uuid.UUID, session_id: int
) -> list[PersonalRecord]:
    """Promote whatever this session beat, and return the new records.

    Does not commit - the caller owns the transaction, so a record and the
    session that produced it land together or not at all.
    """
    new_records: list[PersonalRecord] = []

    for candidate in collect_candidates(db, session_id):
        unit = UNITS[candidate.record_type]

        # The whole decision in one statement. The WHERE guards the update, so
        # a value that does not beat the current best updates nothing and
        # RETURNING comes back empty - which is exactly the signal that this
        # was not a record and nothing belongs in the history either.
        statement = (
            pg_insert(PersonalBest)
            .values(
                user_id=user_id,
                exercise_id=candidate.exercise_id,
                record_type=candidate.record_type,
                value=candidate.value,
                unit=unit,
                session_set_id=candidate.session_set_id,
            )
            .on_conflict_do_update(
                constraint="uq_personal_bests_user_exercise_type",
                set_={
                    "value": candidate.value,
                    "unit": unit,
                    "session_set_id": candidate.session_set_id,
                    "achieved_at": func.now(),
                    "updated_at": func.now(),
                },
                where=PersonalBest.value < candidate.value,
            )
            .returning(PersonalBest.personal_best_id)
        )

        if db.execute(statement).scalar_one_or_none() is None:
            continue

        record = PersonalRecord(
            user_id=user_id,
            exercise_id=candidate.exercise_id,
            record_type=candidate.record_type,
            value=candidate.value,
            unit=unit,
            set_id=candidate.session_set_id,
        )
        db.add(record)
        new_records.append(record)

    return new_records
