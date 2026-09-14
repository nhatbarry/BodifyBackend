from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    chat_message_id: int
    role: str
    content: str
    created_at: datetime


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
