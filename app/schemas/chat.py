from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---- what an answer carries besides its text ----------------------------
#
# Stored as JSON on `chat_messages.attachments` and validated through these
# models on the way in and on the way out. Picture URLs are never stored:
# they are signed on read, like everywhere else in the catalog.

PROPOSAL_PENDING = "pending"
PROPOSAL_APPLIED = "applied"
PROPOSAL_DISMISSED = "dismissed"


class ExerciseCard(BaseModel):
    """One catalog exercise as the chat draws it - tap opens its detail."""

    exercise_id: int
    name: str | None = None
    tracking_type: str = "reps"
    ai_algorithm: str | None = None
    icon_url: str | None = None
    thumbnail_url: str | None = None


class ExerciseSuggestion(BaseModel):
    type: Literal["exercises"] = "exercises"
    reason: str | None = None
    exercises: list[ExerciseCard]


class ProposedExercise(ExerciseCard):
    """An exercise on a proposed date, with `sets` identical target sets."""

    sets: int
    target_reps: int | None = None
    target_weight_kg: float | None = None
    target_duration_seconds: int | None = None
    target_distance_m: float | None = None
    rest_seconds: int | None = None


class ProposedDay(BaseModel):
    date: date
    rest_day: bool = False
    exercises: list[ProposedExercise] = []
    # The date already holds something in the target plan, and applying will
    # replace it. Worked out when the proposal is made, so the card can warn
    # before the tap rather than after.
    replaces_existing: bool = False


class ScheduleProposal(BaseModel):
    type: Literal["schedule"] = "schedule"
    status: Literal["pending", "applied", "dismissed"] = PROPOSAL_PENDING
    summary: str | None = None
    # Null target plan id and a name: applying creates that plan and makes it
    # the active one. Otherwise it writes into the plan with this id.
    plan_id: int | None = None
    plan_name: str | None = None
    creates_plan: bool = False
    days: list[ProposedDay]
    applied_at: datetime | None = None
    applied_dates: list[date] = []
    # Dates already in the past when Apply was tapped. Not written: a plan for
    # a day that has gone is not a plan.
    skipped_dates: list[date] = []


Attachment = Annotated[
    ExerciseSuggestion | ScheduleProposal, Field(discriminator="type")
]


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_message_id: int
    role: str
    content: str
    created_at: datetime
    attachments: list[Attachment] | None = None


class ChatSessionListItem(BaseModel):
    """A row in the history list: enough to choose one, not the whole thread."""

    model_config = ConfigDict(from_attributes=True)

    chat_session_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    # Shown on the row. Counted by the server rather than by loading every
    # message, which is what the list is avoiding in the first place.
    message_count: int = 0


class ChatSessionDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_session_id: int
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ChatMessageRead] = []


class ChatMessageCreate(BaseModel):
    """What the user typed.

    Only the question: the history the model is given is read from the
    database, never from the request. A client that could supply its own
    "previous messages" could put words in the coach's mouth and then quote
    them back - and on a screen that talks about training injuries, that
    matters.
    """

    content: str = Field(min_length=1, max_length=4000)


class ChatSessionCreate(BaseModel):
    """Optional opening message, so starting a chat is one call, not two."""

    content: str | None = Field(default=None, min_length=1, max_length=4000)


class ChatReply(BaseModel):
    """What a send returns: both halves of the exchange, plus where it landed.

    The user's message comes back too - with its server id and timestamp - so
    the client can replace its optimistic copy rather than keep two versions of
    the same sentence.
    """

    chat_session_id: int
    title: str
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class ScheduleApplyResult(BaseModel):
    """What applying a proposal did, and the message as it now reads."""

    plan_id: int
    message: ChatMessageRead
