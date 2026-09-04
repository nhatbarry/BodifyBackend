"""Phase 4: what the training record adds up to.

Three tables, and a deliberate asymmetry between them.

`personal_records` is append-only history - every time a record was broken -
because "40 -> 45 -> 50 kg" is the thing worth showing. `personal_bests` holds
only the current best, one row per (user, exercise, record_type).

That second table is not a cache. Its unique key is what makes "insert only if
this beats the current best" a single atomic statement instead of a
SELECT-then-INSERT with a window in the middle; see `PersonalBest` below. The
history table can therefore stay append-only without ever growing a duplicate,
even when a reopened session is finished twice.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The kinds of record worth keeping. Not every exercise has all of them: a
# Plank has max_duration and nothing else useful, a Farmer's Carry has
# max_distance and max_weight, a Bench Press has the four load-based ones.
MAX_WEIGHT = "max_weight"
MAX_REPS = "max_reps"
MAX_VOLUME = "max_volume"
MAX_DURATION = "max_duration"
MAX_DISTANCE = "max_distance"
ESTIMATED_1RM = "estimated_1rm"

RECORD_TYPES = (
    MAX_WEIGHT,
    MAX_REPS,
    MAX_VOLUME,
    MAX_DURATION,
    MAX_DISTANCE,
    ESTIMATED_1RM,
)

_RECORD_TYPE_SQL = ", ".join(f"'{t}'" for t in RECORD_TYPES)


class PersonalRecord(Base):
    """One moment a record was beaten. Never updated, never deduplicated."""

    __tablename__ = "personal_records"
    __table_args__ = (
        CheckConstraint("value >= 0", name="chk_personal_record_value"),
        CheckConstraint(
            f"record_type IN ({_RECORD_TYPE_SQL})", name="chk_personal_record_type"
        ),
        Index(
            "idx_personal_records_type",
            "user_id",
            "exercise_id",
            "record_type",
            "achieved_at",
        ),
    )

    pr_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE"),
        nullable=False,
    )

    record_type: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)

    achieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # SET NULL, never CASCADE. Deleting one session must not erase months of
    # records - the same rule that keeps a deleted plan from erasing the
    # sessions performed from it.
    set_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("session_sets.session_set_id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PersonalBest(Base):
    """The current best per (user, exercise, record_type).

    The unique constraint is the whole point. It turns the write into:

        INSERT INTO personal_bests (...) VALUES (...)
        ON CONFLICT (user_id, exercise_id, record_type)
        DO UPDATE SET value = EXCLUDED.value, ...
        WHERE personal_bests.value < EXCLUDED.value
        RETURNING personal_best_id

    which decides "is this a record?" inside the database rather than between
    two round trips. No row returned means it was not one, so nothing is
    written to the history either - two concurrent finishes cannot both
    conclude they beat the same 50 kg.
    """

    __tablename__ = "personal_bests"
    __table_args__ = (
        CheckConstraint("value >= 0", name="chk_personal_best_value"),
        CheckConstraint(
            f"record_type IN ({_RECORD_TYPE_SQL})", name="chk_personal_best_type"
        ),
        UniqueConstraint(
            "user_id",
            "exercise_id",
            "record_type",
            name="uq_personal_bests_user_exercise_type",
        ),
        Index("idx_personal_bests_user", "user_id", "exercise_id"),
    )

    personal_best_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    exercise_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("exercises.exercise_id", ondelete="CASCADE"),
        nullable=False,
    )

    record_type: Mapped[str] = mapped_column(String(30), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)

    achieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    session_set_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("session_sets.session_set_id", ondelete="SET NULL")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# What an achievement is measured against. Each one maps to a query over the
# session record, never to a stored counter.
WORKOUT_COUNT = "workout_count"
WORKOUT_STREAK = "workout_streak"
TOTAL_VOLUME = "total_volume"
EXERCISE_DURATION = "exercise_duration"
PERSONAL_RECORD = "personal_record"
PLAN_COMPLETION = "plan_completion"

ACHIEVEMENT_TYPES = (
    WORKOUT_COUNT,
    WORKOUT_STREAK,
    TOTAL_VOLUME,
    EXERCISE_DURATION,
    PERSONAL_RECORD,
    PLAN_COMPLETION,
)

_ACHIEVEMENT_TYPE_SQL = ", ".join(f"'{t}'" for t in ACHIEVEMENT_TYPES)


class Achievement(Base):
    """The catalog. Seeded by `ddl_phase_4.sql`, not created by users."""

    __tablename__ = "achievements"
    __table_args__ = (
        CheckConstraint(
            f"achievement_type IN ({_ACHIEVEMENT_TYPE_SQL})",
            name="chk_achievement_type",
        ),
        CheckConstraint(
            "requirement_value > 0", name="chk_achievement_requirement"
        ),
    )

    achievement_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    # The stable identity. The client maps `code` to a bundled icon, so
    # renaming an asset file never becomes a database migration.
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # NULL in Phase 4 - reserved for the day icons are served from R2 instead
    # of shipped in the app bundle.
    icon_url: Mapped[str | None] = mapped_column(Text)

    achievement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    requirement_value: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )
    requirement_unit: Mapped[str] = mapped_column(String(20), nullable=False)

    # Only for achievements about one movement, e.g. holding a plank. NULL
    # means the requirement is about training in general.
    exercise_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("exercises.exercise_id", ondelete="CASCADE")
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserAchievement(Base):
    """One unlock. The unique key is what makes evaluation safe to repeat.

    Achievements are evaluated on every finish, and a session can be finished
    more than once now that it can be reopened. Without this constraint the
    second finish would award the same badge again; with it the insert is a
    plain ON CONFLICT DO NOTHING.
    """

    __tablename__ = "user_achievements"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "achievement_id", name="uq_user_achievements"
        ),
        Index("idx_user_achievements_user", "user_id", "achieved_at"),
    )

    user_achievement_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )
    achievement_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("achievements.achievement_id", ondelete="CASCADE"),
        nullable=False,
    )

    achieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # What the figure actually was when it unlocked - 7 workouts, 62 seconds.
    # "You held it for 62s" beats "you did it", and it is the evidence behind
    # the row.
    progress_value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
