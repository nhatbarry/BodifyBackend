"""What the AI coach can look up, suggest and propose.

The coach used to see one fixed block of facts (`coach_context`) and could do
nothing with it. These tools let it fetch what a question needs - the catalog,
the calendar, one exercise's progress, the food diary - and hand back two
kinds of result the app draws as cards: exercises it suggests, and a workout
schedule it proposes.

Four rules hold this together:

**The user is the token's, never the model's.** No tool takes a user id; the
toolbox is built for one user by the endpoint and every query is scoped to
that user. A model talked into asking for "user 42's plan" has no parameter
to put it in.

**Ids come from the catalog, never from the model's memory.** Suggestions and
proposals are checked against `exercises` before they are kept. A model will
happily invent "exercise 9001"; the answer it gets back says so and it has
to search instead.

**The model drafts, the user writes.** `propose_workout_schedule` stores a
proposal on the answer and changes nothing in the plan. Applying it is the
user's tap (`apply_proposal`, below), through the same per-date services the
calendar uses - so the rest-day limit, the surviving-row rule and the schedule
rows all hold exactly as they do when a person edits the date by hand. Same
rule as the meal photo analysis.

**A bad call is answered, not raised.** Validation failures go back to the
model as `{"error": ...}` with what to fix, because the model can correct
itself on the next round; an exception would end the answer.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import case, exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.api.endpoints.exercises import catalog_conditions
from app.models.chat import ChatMessage, ChatSession
from app.models.equipment import Equipment
from app.models.exercise_equipment import ExerciseEquipment
from app.models.exercise_muscles import ExerciseMuscle
from app.models.exercise_translations import ExerciseTranslation
from app.models.exercises import Exercise
from app.models.muscle_groups import MuscleGroup
from app.models.nutrition_targets import NutritionTarget
from app.models.progress import PersonalBest
from app.models.schedules import Schedule
from app.models.session_exercises import SessionExercise
from app.models.session_sets import SessionSet
from app.models.workout_days import WorkoutDay
from app.models.workout_exercise_sets import WorkoutExerciseSet
from app.models.workout_exercises import WorkoutExercise
from app.models.workout_plans import WorkoutPlan
from app.models.workout_sessions import ABANDONED, WorkoutSession
from app.schemas.chat import (
    PROPOSAL_APPLIED,
    PROPOSAL_DISMISSED,
    PROPOSAL_PENDING,
    ChatMessageRead,
    ExerciseCard,
    ExerciseSuggestion,
    ProposedDay,
    ProposedExercise,
    ScheduleProposal,
)
from app.services import progress as progress_service
from app.services.exercise_media import list_image_urls
from app.services.exercise_translation import DEFAULT_LANG, best_translation
from app.services.workout_dates import (
    MAX_CONSECUTIVE_REST_DAYS,
    find_day_for_date,
    replace_date_exercises,
    set_date_rest_day,
)

# Rows one search hands the model. Enough to choose from; every row is tokens
# on every later round of the same answer.
MAX_SEARCH_RESULTS = 10
MAX_SUGGESTED = 8

# How far ahead a proposal may reach. Four weeks is a training block; past
# that the user's week will have changed before the plan comes due.
PROPOSAL_HORIZON_DAYS = 28
MAX_EXERCISES_PER_DAY = 12

# Longest range a read tool answers in one call.
MAX_RANGE_DAYS = 31

# Plan created when there is no active plan to write into and the user did
# not name one.
DEFAULT_PLAN_NAME = "Giáo án AI Coach"

_WEEKDAYS_VI = ("Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật")

# Bounds on one proposed target, the same order of magnitude the set editor
# accepts. Wide on purpose - they catch a slipped digit, not a hard workout.
_LIMITS = {
    "sets": (1, 10),
    "reps": (1, 100),
    "weight_kg": (0, 500),
    "duration_seconds": (1, 3600),
    "distance_m": (1, 50000),
    "rest_seconds": (0, 600),
}


class ToolError(ValueError):
    """A call the model can fix. The message is what it is told."""


class ProposalConflict(ValueError):
    """Applying or dismissing is not possible in the proposal's state."""


def _today() -> date:
    return date.today()


def _parse_date(value, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolError(f"{field} must be a date written YYYY-MM-DD, got {value!r}") from None


def _range(args: dict) -> tuple[date, date]:
    start = _parse_date(args.get("from_date"), "from_date")
    end = _parse_date(args.get("to_date"), "to_date")
    if end < start:
        raise ToolError("to_date must not be before from_date")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise ToolError(f"ask for at most {MAX_RANGE_DAYS} days at a time")
    return start, end


def _number(value, field: str, *, integer: bool) -> float | int | None:
    if value is None:
        return None
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError):
        raise ToolError(f"{field} must be a number, got {value!r}") from None
    low, high = _LIMITS[field]
    if not low <= number <= high:
        raise ToolError(f"{field} must be between {low} and {high}, got {number}")
    return number


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:g}"


def _target_text(
    reps: int | None,
    weight: float | None,
    duration: int | None,
    distance: float | None,
) -> str:
    """'10 reps @ 40kg', '45s', '20m' - one planned or performed set, short."""
    bits = []
    if reps is not None:
        bits.append(f"{reps} reps")
    if duration is not None:
        bits.append(f"{duration}s")
    if distance is not None:
        bits.append(f"{_fmt(distance)}m")
    text = " / ".join(bits) or "no target"
    if weight:
        text += f" @ {_fmt(weight)}kg"
    return text


class CoachToolbox:
    """The tools one answer may use, bound to one user.

    Built per request by the chat endpoint. `attachments` collects what the
    tools produced for the answer being written; the endpoint stores them on
    the assistant message once the model has finished.
    """

    def __init__(self, db: Session, user_id: uuid.UUID, language: str = DEFAULT_LANG):
        self.db = db
        self.user_id = user_id
        self.language = language
        self.attachments: list[dict] = []
        self._declarations: list[dict] | None = None

    # ---- what the model is told -----------------------------------------

    @property
    def guide(self) -> str:
        today = _today()
        return (
            "--- CÔNG CỤ ---\n"
            f"Hôm nay là {_WEEKDAYS_VI[today.weekday()]}, {today.isoformat()}.\n"
            "Bạn có các công cụ để tra cứu và hành động. Quy tắc:\n"
            "- Mọi bài tập bạn nhắc tới bằng id PHẢI lấy từ search_exercises; "
            "không bao giờ tự nghĩ ra exercise_id.\n"
            "- Khi gợi ý bài tập cụ thể, gọi show_exercises để người dùng thấy "
            "thẻ bài tập và bấm vào xem chi tiết.\n"
            "- Khi người dùng muốn lên/xếp/đổi lịch tập, xem lịch hiện có bằng "
            "get_schedule rồi gọi propose_workout_schedule. Đề xuất KHÔNG tự "
            "ghi vào lịch: hãy nói người dùng xem thẻ đề xuất và bấm \"Áp dụng\". "
            "Không bao giờ nói là đã lưu lịch.\n"
            "- Một buổi tập toàn thân cần đủ chân, đẩy (ngực/vai), kéo (lưng) "
            "và core: tìm riêng từng nhóm cơ bằng search_exercises rồi mới "
            "chọn, đừng lấy cả buổi từ một lần tìm. Mỗi buổi 4-6 bài.\n"
            f"- Không xếp quá {MAX_CONSECUTIVE_REST_DAYS} ngày nghỉ liên tiếp; "
            "chỉ xếp từ hôm nay trở đi, tối đa "
            f"{PROPOSAL_HORIZON_DAYS} ngày tới.\n"
            "- Bài có tracking_type 'duration' cần duration_seconds, 'distance' "
            "cần distance_m, 'reps' cần reps.\n"
            "- Bài có camera_countable=true đếm rep được bằng camera trong app; "
            "nhắc điều đó khi phù hợp.\n"
            "- Sau khi dùng công cụ, trả lời ngắn gọn bằng ngôn ngữ người dùng. "
            "Gọi bài tập bằng tên, không bao giờ viết exercise_id ra cho "
            "người dùng đọc."
        )

    @property
    def declarations(self) -> list[dict]:
        if self._declarations is None:
            self._declarations = self._build_declarations()
        return self._declarations

    def _build_declarations(self) -> list[dict]:
        # The filter vocabulary is handed over as an enum: a model that has to
        # guess "Quads" vs "Quadriceps" gets an empty search and concludes the
        # catalog has no leg exercises.
        muscles = list(self.db.scalars(select(MuscleGroup.name).order_by(MuscleGroup.name)))
        equipment = list(self.db.scalars(select(Equipment.name).order_by(Equipment.name)))

        date_field = {"type": "string", "description": "YYYY-MM-DD"}
        return [
            {
                "name": "search_exercises",
                "description": (
                    "Search the exercise catalog. Returns ids, names, the "
                    "muscles and equipment each one uses, how it is tracked, "
                    "and whether the app's camera can count its reps."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Words in the exercise name, any language, e.g. 'squat', 'đẩy ngực'.",
                        },
                        "muscle_group": {"type": "string", "enum": muscles},
                        "equipment": {"type": "string", "enum": equipment},
                        "experience_level": {
                            "type": "string",
                            "enum": ["Beginner", "Intermediate", "Advanced"],
                        },
                        "camera_countable": {
                            "type": "boolean",
                            "description": "Only exercises the camera can count.",
                        },
                        "limit": {"type": "integer", "description": f"At most {MAX_SEARCH_RESULTS}."},
                    },
                },
            },
            {
                "name": "show_exercises",
                "description": (
                    "Attach exercise cards to your answer so the user can open "
                    "each one. Use for any concrete exercise suggestion."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "exercise_ids": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "description": f"Ids from search_exercises, at most {MAX_SUGGESTED}.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "One short line on why these, in the user's language.",
                        },
                    },
                    "required": ["exercise_ids"],
                },
            },
            {
                "name": "get_schedule",
                "description": (
                    "The user's active workout plan between two dates: what is "
                    "planned on each date (exercises with target sets, or rest) "
                    "and which workouts were actually done."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"from_date": date_field, "to_date": date_field},
                    "required": ["from_date", "to_date"],
                },
            },
            {
                "name": "get_exercise_progress",
                "description": (
                    "One exercise's training history for the user: best set per "
                    "training day over the last 90 days, and personal records."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"exercise_id": {"type": "integer"}},
                    "required": ["exercise_id"],
                },
            },
            {
                "name": "get_nutrition_summary",
                "description": (
                    "Calories and macros the user logged as eaten per day, and "
                    "their daily calorie target if they set one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"from_date": date_field, "to_date": date_field},
                    "required": ["from_date", "to_date"],
                },
            },
            {
                "name": "propose_workout_schedule",
                "description": (
                    "Propose workouts and rest days on specific dates. Nothing is "
                    "saved: the user sees a card and taps Apply. Writes into the "
                    "active plan unless new_plan_name is given. Each date listed "
                    "replaces what that date currently holds; dates not listed "
                    "are left alone."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "One line describing the plan, in the user's language.",
                        },
                        "new_plan_name": {
                            "type": "string",
                            "description": "Only when the user asks for a separate new plan.",
                        },
                        "days": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "date": date_field,
                                    "rest_day": {"type": "boolean"},
                                    "exercises": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "exercise_id": {"type": "integer"},
                                                "sets": {"type": "integer"},
                                                "reps": {"type": "integer"},
                                                "weight_kg": {"type": "number"},
                                                "duration_seconds": {"type": "integer"},
                                                "distance_m": {"type": "number"},
                                                "rest_seconds": {"type": "integer"},
                                            },
                                            "required": ["exercise_id", "sets"],
                                        },
                                    },
                                },
                                "required": ["date"],
                            },
                        },
                    },
                    "required": ["days"],
                },
            },
        ]

    # ---- dispatch -------------------------------------------------------

    def call(self, name: str, args: dict) -> dict:
        handler = {
            "search_exercises": self.search_exercises,
            "show_exercises": self.show_exercises,
            "get_schedule": self.get_schedule,
            "get_exercise_progress": self.get_exercise_progress,
            "get_nutrition_summary": self.get_nutrition_summary,
            "propose_workout_schedule": self.propose_workout_schedule,
        }.get(name)
        if handler is None:
            return {"error": f"unknown tool {name!r}"}
        try:
            return handler(args or {})
        except ToolError as exc:
            return {"error": str(exc)}

    def fallback_text(self) -> str:
        """What the answer says when the model wrote nothing after its tools."""
        if any(a["type"] == "schedule" for a in self.attachments):
            return "Mình đã soạn lịch tập bên dưới. Xem lại rồi bấm \"Áp dụng\" nếu ổn nhé."
        if self.attachments:
            return "Đây là các bài tập mình gợi ý."
        return "Mình chưa có câu trả lời, bạn hỏi lại giúp mình nhé."

    # ---- catalog --------------------------------------------------------

    def _cards(self, exercise_ids: list[int]) -> dict[int, ExerciseCard]:
        """Catalog rows for these ids, named in the user's language."""
        if not exercise_ids:
            return {}
        translation = best_translation(self.language)
        rows = self.db.execute(
            select(Exercise.exercise_id, translation.c.name, Exercise.tracking_type, Exercise.ai_algorithm)
            .join(translation, translation.c.exercise_id == Exercise.exercise_id, isouter=True)
            .where(Exercise.exercise_id.in_(set(exercise_ids)))
        ).all()
        return {
            exercise_id: ExerciseCard(
                exercise_id=exercise_id,
                name=name,
                tracking_type=tracking_type or "reps",
                ai_algorithm=ai_algorithm,
            )
            for exercise_id, name, tracking_type, ai_algorithm in rows
        }

    def search_exercises(self, args: dict) -> dict:
        limit = args.get("limit") or 8
        try:
            limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        except (TypeError, ValueError):
            limit = 8

        conditions = catalog_conditions(
            muscle_group=args.get("muscle_group") or None,
            equipment=args.get("equipment") or None,
            experience_level=args.get("experience_level") or None,
        )
        # Without a search term the order is the suggestion. By id alone, a
        # bodyweight search opened with the catalog's first rows - crunches and
        # planks - and the first live schedule was three days of abs. Exercises
        # the camera can count come first (they are the app's own selling
        # point), then the beginner-friendly ones.
        order = [
            Exercise.ai_algorithm.is_(None),
            case((func.lower(Exercise.experience_level) == "beginner", 0), else_=1),
            Exercise.exercise_id,
        ]
        query = (args.get("query") or "").strip()
        if query:
            # Any language, unlike the explorer's search box: the model often
            # searches in English ("squat") while the user reads Vietnamese.
            def named(pattern: str):
                return (
                    ExerciseTranslation.exercise_id == Exercise.exercise_id,
                    func.unaccent(ExerciseTranslation.name).ilike(func.unaccent(pattern)),
                )

            conditions.append(exists().where(*named(f"%{query}%")))
            # Ranked like the food search: names starting with the term, then
            # the shortest. By id, "squat" opened with a hip-mobility drill
            # and a smith-machine variant, and the model suggested those.
            shortest = (
                select(func.min(func.length(ExerciseTranslation.name)))
                .where(*named(f"%{query}%"))
                .scalar_subquery()
            )
            order = [
                case((exists().where(*named(f"{query}%")), 0), else_=1),
                shortest,
                Exercise.exercise_id,
            ]
        if args.get("camera_countable"):
            conditions.append(Exercise.ai_algorithm.is_not(None))

        exercises = self.db.scalars(
            select(Exercise).where(*conditions).order_by(*order).limit(limit)
        ).all()
        ids = [e.exercise_id for e in exercises]
        cards = self._cards(ids)

        muscles: dict[int, list[str]] = {}
        for exercise_id, muscle, role in self.db.execute(
            select(ExerciseMuscle.exercise_id, MuscleGroup.name, ExerciseMuscle.role)
            .join(MuscleGroup, MuscleGroup.muscle_id == ExerciseMuscle.muscle_id)
            .where(ExerciseMuscle.exercise_id.in_(ids))
            .order_by(ExerciseMuscle.role)  # 'primary' sorts before 'secondary'
        ).all():
            muscles.setdefault(exercise_id, []).append(muscle)
        equipment: dict[int, list[str]] = {}
        for exercise_id, name in self.db.execute(
            select(ExerciseEquipment.exercise_id, Equipment.name)
            .join(Equipment, Equipment.equipment_id == ExerciseEquipment.equipment_id)
            .where(ExerciseEquipment.exercise_id.in_(ids))
        ).all():
            equipment.setdefault(exercise_id, []).append(name)

        return {
            "results": [
                {
                    "exercise_id": e.exercise_id,
                    "name": cards[e.exercise_id].name if e.exercise_id in cards else None,
                    "muscles": muscles.get(e.exercise_id, []),
                    "equipment": equipment.get(e.exercise_id, []),
                    "level": e.experience_level,
                    "tracking_type": e.tracking_type,
                    "camera_countable": e.ai_algorithm is not None,
                }
                for e in exercises
            ]
        }

    def show_exercises(self, args: dict) -> dict:
        try:
            wanted = list(dict.fromkeys(int(i) for i in args.get("exercise_ids") or []))
        except (TypeError, ValueError):
            raise ToolError("exercise_ids must be a list of integers") from None
        if not wanted:
            raise ToolError("give at least one exercise id")

        cards = self._cards(wanted)
        missing = [i for i in wanted if i not in cards]
        if missing:
            raise ToolError(f"no such exercise id(s): {missing}. Use search_exercises to find real ids.")

        # One suggestion card per answer: a second call adds to it rather than
        # drawing two lists one under the other.
        suggestion = next((a for a in self.attachments if a["type"] == "exercises"), None)
        if suggestion is None:
            suggestion = ExerciseSuggestion(exercises=[]).model_dump(mode="json")
            self.attachments.insert(0, suggestion)
        shown = {c["exercise_id"] for c in suggestion["exercises"]}
        for exercise_id in wanted:
            if exercise_id not in shown and len(suggestion["exercises"]) < MAX_SUGGESTED:
                suggestion["exercises"].append(cards[exercise_id].model_dump(mode="json"))
        if args.get("reason"):
            suggestion["reason"] = str(args["reason"])[:200]

        return {"shown": len(suggestion["exercises"])}

    # ---- the calendar ---------------------------------------------------

    def _active_plan(self) -> WorkoutPlan | None:
        return self.db.scalar(
            select(WorkoutPlan).where(
                WorkoutPlan.user_id == self.user_id,
                WorkoutPlan.is_active.is_(True),
            )
        )

    def get_schedule(self, args: dict) -> dict:
        start, end = _range(args)
        plan = self._active_plan()

        planned: dict[str, dict] = {}
        if plan is not None:
            days = self.db.execute(
                select(Schedule.scheduled_date, WorkoutDay)
                .join(WorkoutDay, WorkoutDay.workout_day_id == Schedule.workout_day_id)
                .where(
                    WorkoutDay.plan_id == plan.plan_id,
                    Schedule.scheduled_date >= start,
                    Schedule.scheduled_date <= end,
                )
                .order_by(Schedule.scheduled_date)
            ).all()
            exercise_ids = [we.exercise_id for _, day in days for we in day.exercises]
            cards = self._cards(exercise_ids)
            for on_date, day in days:
                if day.is_rest_day:
                    planned[on_date.isoformat()] = {"rest_day": True}
                    continue
                planned[on_date.isoformat()] = {
                    "exercises": [
                        {
                            "exercise_id": we.exercise_id,
                            "name": cards[we.exercise_id].name if we.exercise_id in cards else None,
                            "target_sets": [
                                _target_text(
                                    s.target_reps,
                                    float(s.target_weight_kg) if s.target_weight_kg is not None else None,
                                    s.target_duration_seconds,
                                    float(s.target_distance_m) if s.target_distance_m is not None else None,
                                )
                                for s in sorted(we.sets, key=lambda s: s.set_order)
                            ],
                        }
                        for we in sorted(day.exercises, key=lambda we: we.exercise_order)
                    ]
                }

        session_day = func.coalesce(WorkoutSession.scheduled_date, func.date(WorkoutSession.started_at))
        done: dict[str, list[dict]] = {}
        for on_date, status, done_sets, names in self.db.execute(
            select(
                session_day,
                WorkoutSession.status,
                func.count(SessionSet.session_set_id).filter(SessionSet.completed.is_(True)),
                func.array_agg(func.distinct(SessionExercise.exercise_name)),
            )
            .select_from(WorkoutSession)
            .join(SessionExercise, SessionExercise.session_id == WorkoutSession.session_id, isouter=True)
            .join(SessionSet, SessionSet.session_exercise_id == SessionExercise.session_exercise_id, isouter=True)
            .where(
                WorkoutSession.user_id == self.user_id,
                WorkoutSession.status != ABANDONED,
                session_day >= start,
                session_day <= end,
            )
            .group_by(WorkoutSession.session_id, session_day, WorkoutSession.status)
            .order_by(session_day)
        ).all():
            done.setdefault(on_date.isoformat(), []).append(
                {
                    "status": status,
                    "completed_sets": done_sets,
                    "exercises": [n for n in (names or []) if n],
                }
            )

        dates = sorted(set(planned) | set(done))
        return {
            "active_plan": None if plan is None else {"plan_id": plan.plan_id, "name": plan.name},
            "today": _today().isoformat(),
            "days": [
                {"date": d, "planned": planned.get(d), "workouts_done": done.get(d, [])}
                for d in dates
            ],
            "note": "Dates not listed have nothing planned and no workout.",
        }

    # ---- progress -------------------------------------------------------

    def get_exercise_progress(self, args: dict) -> dict:
        try:
            exercise_id = int(args.get("exercise_id"))
        except (TypeError, ValueError):
            raise ToolError("exercise_id must be an integer") from None
        cards = self._cards([exercise_id])
        if exercise_id not in cards:
            raise ToolError(f"no such exercise id {exercise_id}")

        points = progress_service.exercise_progression(
            self.db, self.user_id, exercise_id, from_date=_today() - timedelta(days=90)
        )
        bests = self.db.scalars(
            select(PersonalBest).where(
                PersonalBest.user_id == self.user_id,
                PersonalBest.exercise_id == exercise_id,
            )
        ).all()
        return {
            "exercise": cards[exercise_id].name,
            # The latest twenty: enough to read a trend, and the oldest are the
            # least relevant to what the user should do next.
            "training_days": [
                {
                    "date": p.day.isoformat(),
                    "best_set": _target_text(p.best_reps, p.best_weight_kg, p.best_duration_seconds, p.best_distance_m),
                    "volume_kg": round(p.volume_kg, 1),
                }
                for p in points[-20:]
            ],
            "personal_records": [
                {"type": b.record_type, "value": float(b.value), "unit": b.unit, "date": b.achieved_at.date().isoformat()}
                for b in bests
            ],
        }

    def get_nutrition_summary(self, args: dict) -> dict:
        # The endpoint's own aggregation, so "what I ate" means the same thing
        # to the coach as to the history screen (eaten items only).
        from app.api.endpoints.nutrition import daily_summary

        start, end = _range(args)
        days = daily_summary(
            current_user=SimpleNamespace(id=self.user_id),
            db=self.db,
            date_from=start,
            date_to=end,
        )
        target = self.db.scalar(
            select(NutritionTarget).where(NutritionTarget.user_id == self.user_id)
        )
        return {
            "daily_target_kcal": float(target.energy_kcal) if target else None,
            "days": [
                {
                    "date": d.logged_date.isoformat(),
                    "kcal": round(float(d.energy_kcal)),
                    "protein_g": round(float(d.protein_g)),
                    "fat_g": round(float(d.fat_g)),
                    "carb_g": round(float(d.carb_g)),
                }
                for d in days
            ],
            "note": "Days not listed had nothing logged as eaten - not zero intake.",
        }

    # ---- proposing a schedule -------------------------------------------

    def propose_workout_schedule(self, args: dict) -> dict:
        raw_days = args.get("days") or []
        if not isinstance(raw_days, list) or not raw_days:
            raise ToolError("days must be a non-empty list")
        if len(raw_days) > PROPOSAL_HORIZON_DAYS:
            raise ToolError(f"propose at most {PROPOSAL_HORIZON_DAYS} dates")

        today = _today()
        horizon = today + timedelta(days=PROPOSAL_HORIZON_DAYS - 1)

        parsed: list[tuple[date, bool, list[dict]]] = []
        seen: set[date] = set()
        for raw in raw_days:
            on_date = _parse_date(raw.get("date"), "date")
            if on_date < today or on_date > horizon:
                raise ToolError(
                    f"{on_date} is outside {today} .. {horizon}; schedule from today onwards"
                )
            if on_date in seen:
                raise ToolError(f"{on_date} is listed twice")
            seen.add(on_date)
            exercises = raw.get("exercises") or []
            rest = bool(raw.get("rest_day"))
            if rest and exercises:
                raise ToolError(f"{on_date}: a rest day cannot have exercises")
            if not rest and not exercises:
                raise ToolError(f"{on_date}: give exercises, or set rest_day true")
            if len(exercises) > MAX_EXERCISES_PER_DAY:
                raise ToolError(f"{on_date}: at most {MAX_EXERCISES_PER_DAY} exercises a day")
            parsed.append((on_date, rest, exercises))
        parsed.sort(key=lambda p: p[0])

        # Within the proposal itself; the plan's existing rest days are checked
        # again when it is applied, by the same rule the calendar uses.
        run = 0
        previous: date | None = None
        for on_date, rest, _ in parsed:
            if rest:
                run = run + 1 if previous == on_date - timedelta(days=1) else 1
                previous = on_date
                if run > MAX_CONSECUTIVE_REST_DAYS:
                    raise ToolError(f"more than {MAX_CONSECUTIVE_REST_DAYS} rest days in a row")
            else:
                run = 0
                previous = None

        all_ids: list[int] = []
        for _, _, exercises in parsed:
            for item in exercises:
                try:
                    all_ids.append(int(item.get("exercise_id")))
                except (TypeError, ValueError):
                    raise ToolError("every exercise needs an integer exercise_id") from None
        cards = self._cards(all_ids)
        missing = sorted({i for i in all_ids if i not in cards})
        if missing:
            raise ToolError(f"no such exercise id(s): {missing}. Use search_exercises to find real ids.")

        new_plan_name = (args.get("new_plan_name") or "").strip()[:150] or None
        plan = None if new_plan_name else self._active_plan()

        days: list[ProposedDay] = []
        for on_date, rest, exercises in parsed:
            proposed: list[ProposedExercise] = []
            for item in exercises:
                card = cards[int(item["exercise_id"])]
                if any(p.exercise_id == card.exercise_id for p in proposed):
                    raise ToolError(f"{on_date}: {card.name} is listed twice")
                entry = ProposedExercise(
                    **card.model_dump(),
                    sets=_number(item.get("sets"), "sets", integer=True) or 1,
                    target_reps=_number(item.get("reps"), "reps", integer=True),
                    target_weight_kg=_number(item.get("weight_kg"), "weight_kg", integer=False),
                    target_duration_seconds=_number(item.get("duration_seconds"), "duration_seconds", integer=True),
                    target_distance_m=_number(item.get("distance_m"), "distance_m", integer=False),
                    rest_seconds=_number(item.get("rest_seconds"), "rest_seconds", integer=True),
                )
                _check_axis(entry)
                proposed.append(entry)

            existing = find_day_for_date(self.db, plan.plan_id, on_date) if plan else None
            days.append(
                ProposedDay(
                    date=on_date,
                    rest_day=rest,
                    exercises=proposed,
                    replaces_existing=existing is not None,
                )
            )

        proposal = ScheduleProposal(
            summary=(str(args["summary"])[:200] if args.get("summary") else None),
            plan_id=plan.plan_id if plan else None,
            plan_name=plan.name if plan else (new_plan_name or DEFAULT_PLAN_NAME),
            creates_plan=plan is None,
            days=days,
        )

        # A second proposal in the same answer is the model revising its first,
        # not a second plan - keep the latest.
        self.attachments = [a for a in self.attachments if a["type"] != "schedule"]
        self.attachments.append(proposal.model_dump(mode="json"))

        return {
            "status": "proposed - NOT saved yet; the user must tap Apply on the card",
            "dates": len(days),
            "target_plan": proposal.plan_name,
            "creates_new_plan": proposal.creates_plan,
            "replaces_existing_dates": [d.date.isoformat() for d in days if d.replaces_existing],
        }


def _check_axis(entry: ProposedExercise) -> None:
    """The target has to be on the axis the exercise is measured in.

    A Plank planned as "3 x 10 reps" would be logged against a target the set
    editor never shows, and performance would read as 0%.
    """
    needs = {
        "reps": ("reps", entry.target_reps is not None),
        "duration": ("duration_seconds", entry.target_duration_seconds is not None),
        "distance": ("distance_m", entry.target_distance_m is not None),
        "reps_duration": (
            "reps or duration_seconds",
            entry.target_reps is not None or entry.target_duration_seconds is not None,
        ),
    }
    field, present = needs.get(entry.tracking_type, needs["reps"])
    if not present:
        raise ToolError(
            f"{entry.name} (id {entry.exercise_id}) is tracked by "
            f"{entry.tracking_type}; give {field}"
        )


# ---- after the answer: reading and applying ------------------------------


def history_note(attachments: list[dict] | None) -> str:
    """A line appended to an old answer when it is replayed to the model.

    The model sees past answers as text only, so without this it would not
    know it proposed a schedule two messages ago, or that the user applied it.
    Not stored - the message keeps exactly what the user was shown.
    """
    if not attachments:
        return ""
    notes = []
    for attachment in attachments:
        if attachment.get("type") == "exercises":
            names = ", ".join(c.get("name") or f"#{c['exercise_id']}" for c in attachment["exercises"])
            notes.append(f"[Đã gợi ý bài tập: {names}]")
        elif attachment.get("type") == "schedule":
            days = attachment.get("days") or []
            span = f"{days[0]['date']} → {days[-1]['date']}" if days else ""
            status = {
                PROPOSAL_PENDING: "chưa áp dụng",
                PROPOSAL_APPLIED: "người dùng ĐÃ áp dụng",
                PROPOSAL_DISMISSED: "người dùng đã bỏ qua",
            }.get(attachment.get("status"), attachment.get("status"))
            notes.append(f"[Đã đề xuất lịch {len(days)} ngày {span}, {status}]")
    return "\n" + "\n".join(notes)


def message_reads(db: Session, messages: list[ChatMessage]) -> list[ChatMessageRead]:
    """Messages as the API returns them, with card pictures signed.

    URLs are never stored in an attachment - they expire - so they are added
    here, for every card in every message, in one query.
    """
    reads = [ChatMessageRead.model_validate(m) for m in messages]
    cards: list[ExerciseCard] = []
    for read in reads:
        for attachment in read.attachments or []:
            if isinstance(attachment, ExerciseSuggestion):
                cards += attachment.exercises
            else:
                cards += [e for day in attachment.days for e in day.exercises]
    images = list_image_urls(db, list({c.exercise_id for c in cards}))
    for card in cards:
        card.icon_url, card.thumbnail_url = images.get(card.exercise_id, (None, None))
    return reads


def owned_message_for_update(
    db: Session, chat_message_id: int, user_id: uuid.UUID
) -> ChatMessage | None:
    """The message, locked, if it belongs to this user's conversation.

    Locked because applying is check-then-write: two taps on Apply from two
    devices must not both see `pending`.
    """
    return db.scalar(
        select(ChatMessage)
        .join(ChatSession, ChatSession.chat_session_id == ChatMessage.chat_session_id)
        .where(
            ChatMessage.chat_message_id == chat_message_id,
            ChatSession.user_id == user_id,
        )
        .with_for_update(of=ChatMessage)
    )


def _proposal_index(message: ChatMessage) -> int:
    for index, attachment in enumerate(message.attachments or []):
        if attachment.get("type") == "schedule":
            return index
    raise LookupError("this message has no schedule proposal")


def dismiss_proposal(message: ChatMessage) -> None:
    index = _proposal_index(message)
    proposal = ScheduleProposal.model_validate(message.attachments[index])
    if proposal.status != PROPOSAL_PENDING:
        raise ProposalConflict(f"This proposal was already {proposal.status}")
    proposal.status = PROPOSAL_DISMISSED
    message.attachments[index] = proposal.model_dump(mode="json")
    flag_modified(message, "attachments")


def apply_proposal(db: Session, message: ChatMessage, user_id: uuid.UUID) -> int:
    """Writes a pending proposal into the plan. Returns the plan id.

    One transaction, owned by the caller: a rest-day rule that fails on the
    fifth date rolls back the first four rather than leaving half a week.

    Workout dates are written before rest dates. A rest run is measured
    against what the plan holds at that moment, and a proposal that turns an
    old rest day into a workout should get credit for it before its own rest
    days are counted.
    """
    index = _proposal_index(message)
    proposal = ScheduleProposal.model_validate(message.attachments[index])
    if proposal.status != PROPOSAL_PENDING:
        raise ProposalConflict(f"This proposal was already {proposal.status}")

    plan = _target_plan(db, proposal, user_id)

    today = _today()
    to_write = [d for d in proposal.days if d.date >= today]
    skipped = [d.date for d in proposal.days if d.date < today]
    if not to_write:
        raise ProposalConflict("Every date in this proposal has already passed")

    found = set(
        db.scalars(
            select(Exercise.exercise_id).where(
                Exercise.exercise_id.in_({e.exercise_id for d in to_write for e in d.exercises})
            )
        ).all()
    )

    for day in (d for d in to_write if not d.rest_day):
        exercises = [e for e in day.exercises if e.exercise_id in found]
        existing = find_day_for_date(db, plan.plan_id, day.date)
        if existing is not None and existing.is_rest_day and not existing.exercises:
            set_date_rest_day(db, plan, user_id, day.date, False)
            db.flush()
        workout_day = replace_date_exercises(
            db, plan, user_id, day.date, [e.exercise_id for e in exercises]
        )
        if workout_day is None:
            continue
        rows = {
            we.exercise_id: we
            for we in db.scalars(
                select(WorkoutExercise).where(
                    WorkoutExercise.workout_day_id == workout_day.workout_day_id
                )
            ).all()
        }
        # The proposal names the sets, so they replace whatever targets a
        # surviving exercise had - that is what the user saw on the card.
        db.execute(
            WorkoutExerciseSet.__table__.delete().where(
                WorkoutExerciseSet.workout_exercise_id.in_([r.workout_exercise_id for r in rows.values()])
            )
        )
        for entry in exercises:
            row = rows[entry.exercise_id]
            for order in range(1, entry.sets + 1):
                db.add(
                    WorkoutExerciseSet(
                        workout_exercise_id=row.workout_exercise_id,
                        set_order=order,
                        target_reps=entry.target_reps,
                        target_weight_kg=entry.target_weight_kg,
                        target_duration_seconds=entry.target_duration_seconds,
                        target_distance_m=entry.target_distance_m,
                        rest_seconds=entry.rest_seconds,
                    )
                )
        db.flush()

    for day in (d for d in to_write if d.rest_day):
        existing = find_day_for_date(db, plan.plan_id, day.date)
        if existing is not None and existing.exercises:
            replace_date_exercises(db, plan, user_id, day.date, [])
            db.flush()
        # TooManyRestDays propagates: the caller turns it into a 409.
        set_date_rest_day(db, plan, user_id, day.date, True)
        db.flush()

    proposal.status = PROPOSAL_APPLIED
    proposal.plan_id = plan.plan_id
    proposal.plan_name = plan.name
    proposal.applied_at = datetime.now(timezone.utc)
    proposal.applied_dates = [d.date for d in to_write]
    proposal.skipped_dates = skipped
    message.attachments[index] = proposal.model_dump(mode="json")
    flag_modified(message, "attachments")
    return plan.plan_id


def _target_plan(db: Session, proposal: ScheduleProposal, user_id: uuid.UUID) -> WorkoutPlan:
    """The plan a proposal writes into, created and activated if it is new."""
    if not proposal.creates_plan:
        plan = db.scalar(
            select(WorkoutPlan).where(
                WorkoutPlan.plan_id == proposal.plan_id,
                WorkoutPlan.user_id == user_id,
            )
        )
        if plan is None:
            raise ProposalConflict("The plan this proposal was for no longer exists")
        return plan

    plan = WorkoutPlan(
        user_id=user_id,
        name=proposal.plan_name or DEFAULT_PLAN_NAME,
        description=proposal.summary,
        start_date=min(d.date for d in proposal.days),
    )
    # The user asked for this plan to be the one they follow. The clear comes
    # first: the one-active index is not deferrable.
    db.execute(
        WorkoutPlan.__table__.update()
        .where(WorkoutPlan.user_id == user_id, WorkoutPlan.is_active.is_(True))
        .values(is_active=False)
    )
    db.flush()
    plan.is_active = True
    db.add(plan)
    db.flush()
    return plan
