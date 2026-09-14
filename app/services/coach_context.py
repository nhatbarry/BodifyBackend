"""What the AI coach is allowed to know about the user.

The coach used to answer in the abstract because it could not see anything.
This assembles the facts it needs and hands them over as a short block of
text that goes in front of every conversation.

Three rules shaped it:

**Summaries, not tables.** "7 ngày qua: 3 buổi, 12 set, 4.2 tấn" costs twenty
tokens; the sets behind it cost hundreds and say the same thing to a coach.
Every request pays for this block, so it stays small - which also means less
of the user's training record leaves the server than a naive dump would send.

**Silence is a fact.** A user with no body metrics gets a line saying so,
rather than that section vanishing. A model given a prompt with no weight in
it will cheerfully invent one; a model told "chưa ghi cân nặng" asks for it.

**Nothing new is queried.** Every number here already has a service that
computes it for the progress screens - `progress.summary`, `personal_bests`,
the active plan. Writing fresh SQL would mean two definitions of "total
volume" that drift apart, and the screens would eventually disagree with the
coach about the user's own training.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.body_metrics import BodyMetric
from app.models.progress import PersonalBest
from app.models.schedules import Schedule
from app.models.user_profiles import UserProfile
from app.models.workout_days import WorkoutDay
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan
from app.services import progress as progress_service
from app.services.exercise_translation import DEFAULT_LANG, best_translation

# How far back "recently" goes. Four weeks is long enough to show a habit and
# short enough that last month's injury break does not read as current form.
RECENT_DAYS = 28

# Records shown. The point is to tell the coach roughly how strong the user is,
# not to list every lift they have ever done.
MAX_RECORDS = 6


def _age(born: date | None) -> int | None:
    if born is None:
        return None
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _profile_lines(db: Session, user_id: uuid.UUID) -> list[str]:
    profile = db.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    )
    if profile is None:
        return ["- Hồ sơ: chưa có"]

    bits = []
    age = _age(profile.date_of_birth)
    if age is not None:
        bits.append(f"{age} tuổi")
    if profile.gender:
        bits.append(profile.gender)

    return [f"- Người dùng: {profile.name}" + (f" ({', '.join(bits)})" if bits else "")]


def _body_lines(db: Session, user_id: uuid.UUID) -> list[str]:
    metric = db.scalar(
        select(BodyMetric)
        .where(BodyMetric.user_id == user_id)
        .order_by(BodyMetric.recorded_at.desc())
        .limit(1)
    )
    if metric is None:
        return ["- Chỉ số cơ thể: chưa ghi nhận lần nào"]

    parts = []
    if metric.height_cm:
        parts.append(f"cao {metric.height_cm:.0f}cm")
    if metric.weight_kg:
        parts.append(f"nặng {metric.weight_kg:.1f}kg")

    # Computed here as it is everywhere else in this codebase, never stored.
    if metric.height_cm and metric.weight_kg and metric.height_cm > 0:
        height_m = float(metric.height_cm) / 100
        bmi = float(metric.weight_kg) / (height_m * height_m)
        parts.append(f"BMI {bmi:.1f}")

    if not parts:
        return ["- Chỉ số cơ thể: chưa ghi nhận lần nào"]

    when = metric.recorded_at.date().isoformat()
    return [f"- Chỉ số cơ thể ({when}): {', '.join(parts)}"]


def _training_lines(db: Session, user_id: uuid.UUID) -> list[str]:
    since = date.today() - timedelta(days=RECENT_DAYS)
    stats = progress_service.summary(db, user_id, from_date=since)

    if stats.session_count == 0:
        return [f"- Tập luyện {RECENT_DAYS} ngày qua: chưa có buổi nào hoàn thành"]

    line = (
        f"- Tập luyện {RECENT_DAYS} ngày qua: {stats.session_count} buổi, "
        f"tổng khối lượng {stats.total_volume_kg:,.0f}kg"
    )
    if stats.workouts_per_week is not None:
        line += f", trung bình {stats.workouts_per_week:.1f} buổi/tuần"

    lines = [line]

    # How much of the plan is actually being done - the number a coach would
    # ask about before suggesting more work.
    if stats.completion_percent is not None:
        lines.append(
            f"- Hoàn thành so với giáo án: {stats.completion_percent:.0f}% "
            f"({stats.completed_planned_sets}/{stats.planned_sets} set)"
        )
    return lines


def _plan_lines(db: Session, user_id: uuid.UUID, language: str) -> list[str]:
    plan = db.scalar(
        select(WorkoutPlan).where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
        )
    )
    if plan is None:
        return ["- Giáo án: chưa có giáo án nào đang dùng"]

    lines = [f"- Giáo án đang theo: {plan.name}" + (f" (mục tiêu: {plan.goal})" if plan.goal else "")]

    # What is on for today, which is the single most useful fact when the user
    # asks "hôm nay tập gì".
    today = db.scalar(
        select(WorkoutDay)
        .join(Schedule, Schedule.workout_day_id == WorkoutDay.workout_day_id)
        .where(
            WorkoutDay.plan_id == plan.plan_id,
            Schedule.scheduled_date == date.today(),
        )
        .limit(1)
    )
    if today is None:
        lines.append("- Hôm nay: không có buổi nào được xếp lịch")
    elif today.is_rest_day:
        lines.append("- Hôm nay: ngày nghỉ theo giáo án")
    else:
        # The exercises, not the day's name. Days created from the calendar
        # are named with the ISO date, which is an identifier and not a label -
        # the coach was reading that back as "buổi 2026-09-14" and then saying
        # it did not know what the user was training.
        lines.append(f"- Hôm nay: {_day_exercises(db, today, language)}")

    return lines


def _day_exercises(db: Session, day: WorkoutDay, language: str) -> str:
    """The exercises scheduled for one day, named in the user's language."""
    translation = best_translation(language)
    names = db.execute(
        select(translation.c.name)
        .join(
            WorkoutExercise,
            WorkoutExercise.exercise_id == translation.c.exercise_id,
        )
        .where(WorkoutExercise.workout_day_id == day.workout_day_id)
        .order_by(WorkoutExercise.exercise_order)
    ).scalars().all()

    if not names:
        return f"buổi \"{day.name}\" (chưa có bài nào)"
    return ", ".join(n for n in names if n)


def _record_lines(
    db: Session, user_id: uuid.UUID, language: str
) -> list[str]:
    bests = db.scalars(
        select(PersonalBest)
        .where(PersonalBest.user_id == user_id)
        .order_by(PersonalBest.updated_at.desc())
        .limit(MAX_RECORDS)
    ).all()
    if not bests:
        return ["- Kỷ lục cá nhân: chưa có"]

    translation = best_translation(language)
    names = dict(
        db.execute(
            select(translation.c.exercise_id, translation.c.name).where(
                translation.c.exercise_id.in_({b.exercise_id for b in bests})
            )
        ).all()
    )

    items = [
        f"{names.get(b.exercise_id, f'#{b.exercise_id}')} {b.record_type} "
        f"{b.value:g}{b.unit}"
        for b in bests
    ]
    return [f"- Kỷ lục cá nhân: {'; '.join(items)}"]


def build(db: Session, user_id: uuid.UUID, language: str = DEFAULT_LANG) -> str:
    """The user's training picture, as a block of text for the prompt.

    Always returns something. A brand-new account produces a block saying it
    has no data yet, which is a fact the coach needs - not an empty string that
    leaves it guessing.
    """
    lines: list[str] = []
    lines += _profile_lines(db, user_id)
    lines += _body_lines(db, user_id)
    lines += _training_lines(db, user_id)
    lines += _plan_lines(db, user_id, language)
    lines += _record_lines(db, user_id, language)

    return "\n".join(lines)
