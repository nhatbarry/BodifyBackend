"""Phase 6: the AI coach's chat.

Three rules hold this together, and they are the reason the model is called
here rather than from the app:

1. **The key never leaves the server.** The Kotlin prototype shipped it inside
   the APK, where anyone can read it and spend it.
2. **The history comes from the database, never from the request.** A client
   that could supply its own "previous messages" could put words in the
   coach's mouth and quote them back - on a screen that discusses injuries,
   that is not a theoretical problem.
3. **The user's message is saved before the model is asked.** A reply that
   fails leaves the question in the conversation, so the user can see what
   they asked and try again, rather than watching it vanish.

What the coach is allowed to *know* - training history, body metrics, the
active plan - is the next slice. Nothing here changes when it lands: it is a
bigger prompt, built from the same session.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentUserDep, DbSession
from app.models.chat import ASSISTANT, USER, ChatMessage, ChatSession
from app.schemas.chat import (
    ChatMessageCreate,
    ChatMessageRead,
    ChatReply,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionListItem,
)
from app.services.gemini import AiCoachUnavailable, generate_reply

router = APIRouter(tags=["ai-coach"])

# How much of the conversation the model is shown. Long enough that it follows
# a real back-and-forth, short enough that an hour-long chat does not grow the
# cost of every further message without limit. Trimmed from the oldest end -
# what was said most recently is what the next answer depends on.
HISTORY_LIMIT = 30

# A history row has to be readable at a glance, and the first question is what
# the conversation was about.
TITLE_LENGTH = 60


def _owned_session(
    db: Session, chat_session_id: int, user_id: uuid.UUID
) -> ChatSession:
    """Resolves a conversation, or 404s.

    Ownership is in the WHERE clause, so somebody else's conversation is
    indistinguishable from one that does not exist - the same rule the planning
    and session routes follow.
    """
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.chat_session_id == chat_session_id,
            ChatSession.user_id == user_id,
        )
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return session


def _title_from(question: str) -> str:
    """The first question, trimmed to fit a list row."""
    cleaned = " ".join(question.split())
    if len(cleaned) <= TITLE_LENGTH:
        return cleaned
    return f"{cleaned[:TITLE_LENGTH].rstrip()}…"


async def _answer(
    db: Session, session: ChatSession, content: str
) -> ChatReply:
    """Saves the question, asks the model, saves the answer."""
    question = ChatMessage(
        chat_session_id=session.chat_session_id, role=USER, content=content
    )
    db.add(question)

    if not session.title:
        session.title = _title_from(content)

    # Committed before the model is called, so a failure below leaves the
    # question in the conversation rather than losing it.
    db.commit()
    db.refresh(question)
    db.refresh(session)

    history = [
        (role, text)
        for role, text in db.execute(
            select(ChatMessage.role, ChatMessage.content)
            .where(
                ChatMessage.chat_session_id == session.chat_session_id,
                # The question just saved is passed separately, not twice.
                ChatMessage.chat_message_id != question.chat_message_id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    ][::-1]  # oldest first, which is the order a conversation is read in

    try:
        reply_text = await generate_reply(history, content)
    except AiCoachUnavailable as exc:
        # 503, not 500: nothing is wrong with the request, and the client
        # should offer to try again rather than report a bug.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    answer = ChatMessage(
        chat_session_id=session.chat_session_id,
        role=ASSISTANT,
        content=reply_text,
    )
    db.add(answer)
    session.updated_at = func.now()
    db.commit()
    db.refresh(answer)
    db.refresh(session)

    return ChatReply(
        chat_session_id=session.chat_session_id,
        title=session.title,
        user_message=ChatMessageRead.model_validate(question),
        assistant_message=ChatMessageRead.model_validate(answer),
    )


@router.get("/ai-coach/sessions", response_model=list[ChatSessionListItem])
def list_chat_sessions(
    current_user: CurrentUserDep,
    db: DbSession,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[ChatSessionListItem]:
    """Past conversations, most recently used first.

    The message count is a GROUP BY here rather than a length in the client,
    which would mean loading every message of every conversation to draw a list
    that shows none of them.
    """
    counts = (
        select(
            ChatMessage.chat_session_id.label("session_id"),
            func.count().label("total"),
        )
        .group_by(ChatMessage.chat_session_id)
        .subquery()
    )

    rows = db.execute(
        select(ChatSession, func.coalesce(counts.c.total, 0))
        .join(counts, counts.c.session_id == ChatSession.chat_session_id, isouter=True)
        .where(ChatSession.user_id == current_user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    ).all()

    return [
        ChatSessionListItem(
            chat_session_id=session.chat_session_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            message_count=int(total or 0),
        )
        for session, total in rows
    ]


@router.get(
    "/ai-coach/sessions/{chat_session_id}", response_model=ChatSessionDetail
)
def get_chat_session(
    chat_session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ChatSessionDetail:
    """One conversation with everything said in it, oldest first."""
    session = _owned_session(db, chat_session_id, current_user.id)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.chat_session_id == session.chat_session_id)
        .order_by(ChatMessage.created_at)
    ).all()

    return ChatSessionDetail(
        chat_session_id=session.chat_session_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=[ChatMessageRead.model_validate(m) for m in messages],
    )


@router.post(
    "/ai-coach/sessions",
    response_model=ChatReply | ChatSessionDetail,
    status_code=status.HTTP_201_CREATED,
)
async def start_chat_session(
    payload: ChatSessionCreate,
    current_user: CurrentUserDep,
    db: DbSession,
):
    """Starts a conversation, optionally with its first question.

    Sending the opening message here rather than in a second call means the
    common path - open the screen, type, send - is one round trip, and there is
    no window in which an empty conversation exists in somebody's history
    because they changed their mind.
    """
    session = ChatSession(user_id=current_user.id, title="")
    db.add(session)
    db.commit()
    db.refresh(session)

    if payload.content is None:
        return ChatSessionDetail(
            chat_session_id=session.chat_session_id,
            title=session.title,
            created_at=session.created_at,
            updated_at=session.updated_at,
            messages=[],
        )

    return await _answer(db, session, payload.content)


@router.post(
    "/ai-coach/sessions/{chat_session_id}/messages", response_model=ChatReply
)
async def send_chat_message(
    chat_session_id: int,
    payload: ChatMessageCreate,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ChatReply:
    """Asks the coach something in an existing conversation."""
    session = _owned_session(db, chat_session_id, current_user.id)
    return await _answer(db, session, payload.content)


@router.delete(
    "/ai-coach/sessions/{chat_session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_chat_session(
    chat_session_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> Response:
    """Removes a conversation and everything said in it.

    A hard delete: a conversation the user asked to be rid of should not sit in
    the database flagged as hidden, and nothing else references these rows.
    """
    session = _owned_session(db, chat_session_id, current_user.id)
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.chat_session_id == session.chat_session_id
        )
    )
    db.delete(session)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
