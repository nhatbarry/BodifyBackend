"""Position management for the three ordered planning tables.

`workout_days.day_order`, `workout_exercises.exercise_order` and
`workout_exercise_sets.set_order` each carry a UNIQUE constraint scoped to
their parent. Those constraints are DEFERRABLE INITIALLY DEFERRED, which means
they are checked at COMMIT rather than per statement - and that is the whole
reason reordering is a bulk operation instead of a series of PUTs.

Two separate requests are two separate transactions, so each commits on its
own and the first one to move an item onto an occupied position fails:

    UPDATE workout_exercises SET exercise_order=1 WHERE workout_exercise_id=9992;
    ERROR: duplicate key value violates unique constraint "uq_workout_exercise_order"

Sending the whole list in one request keeps every intermediate state inside a
single transaction, where the duplicate is allowed to exist briefly.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def lock_parent(db: Session, pk_column, pk_value: int) -> None:
    """Serialise appends against one parent row.

    Two clients appending to the same day would otherwise both read the same
    MAX(order) and both try to insert that position + 1. Taking the parent's
    row lock first makes them queue instead, which is cheaper to reason about
    than catching the unique violation and retrying, and it does not block
    work on any other day.
    """
    db.execute(select(pk_column).where(pk_column == pk_value).with_for_update())


def next_order(db: Session, order_column, *conditions) -> int:
    """Position that appends to the end. Call after lock_parent()."""
    current_max = db.scalar(select(func.max(order_column)).where(*conditions))
    return (current_max or 0) + 1


def apply_reorder(
    db: Session,
    model,
    pk_column,
    order_column,
    parent_condition,
    requested: dict[int, int],
    label: str,
) -> None:
    """Rewrite every position under one parent, in a single transaction.

    The request must list every child exactly once. A partial list is rejected
    rather than guessed at: the items left out would keep positions that
    collide with the ones being moved, and there is no correct way to invent
    where they should go.

    Positions are then normalised to 1..n by the requested ordering, so a
    client that sends 1, 2, 50 gets 1, 2, 3 and the sequence never drifts
    into gaps that make appends unpredictable.
    """
    existing = set(db.scalars(select(pk_column).where(parent_condition)).all())
    given = set(requested)

    if given != existing:
        missing = sorted(existing - given)
        unknown = sorted(given - existing)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"not in this {label} {unknown}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Reorder must list every item exactly once: {', '.join(details)}",
        )

    if len(set(requested.values())) != len(requested):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Reorder positions must be distinct",
        )

    ordered_ids = sorted(requested, key=lambda item_id: requested[item_id])
    for position, item_id in enumerate(ordered_ids, start=1):
        db.execute(
            update(model)
            .where(pk_column == item_id)
            .values({order_column.key: position})
        )
