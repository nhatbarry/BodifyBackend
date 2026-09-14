"""Phase 6: the AI coach's conversations.

A session owns its messages and they die with it - `ON DELETE CASCADE`, the
opposite of the rule everywhere in Phase 3. A workout is a record of something
that happened and outlives the plan it came from; a message outside its
conversation is not evidence of anything.

`role` is this application's vocabulary, not the model provider's. Gemini calls
the two sides "user" and "model"; another provider calls them something else.
Mapping happens at the boundary in `app/services/gemini.py`, so changing
provider never becomes a migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

USER = "user"
ASSISTANT = "assistant"

ROLES = (USER, ASSISTANT)

_ROLE_SQL = ", ".join(f"'{r}'" for r in ROLES)


class ChatSession(Base):
    """One conversation with the coach."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("idx_chat_sessions_user", "user_id", "updated_at"),
    )

    chat_session_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Derived from the first thing the user asked, on the server, so every
    # client names the same conversation the same way. Stored rather than
    # computed on read: the first message can be deleted later, and a history
    # row that renames itself is one nobody trusts.
    title: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=""
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped by every message: "most recent conversation" is the only order a
    # chat history is read in.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    """One thing said, by one side of the conversation."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(f"role IN ({_ROLE_SQL})", name="chk_chat_message_role"),
        CheckConstraint("length(content) > 0", name="chk_chat_message_content"),
        Index("idx_chat_messages_session", "chat_session_id", "created_at"),
    )

    chat_message_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_sessions.chat_session_id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")
