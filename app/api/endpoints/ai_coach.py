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

What the coach can *do* lives in `app/services/coach_tools.py`: look things
up, suggest exercises, propose a schedule. What it produces besides text is
stored on the answer as `attachments`, and a proposed schedule reaches the
plan only through the apply route below - the user's tap, never the model's.
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
    ScheduleApplyResult,
)
from app.services import coach_context, coach_tools
from app.services.exercise_translation import DEFAULT_LANG
from app.services.gemini import (
    AiCoachQuotaExceeded,
    AiCoachUnavailable,
    generate_reply,
)
from app.services.workout_dates import MAX_CONSECUTIVE_REST_DAYS, TooManyRestDays

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
    db: Session,
    session: ChatSession,
    content: str,
    language: str = DEFAULT_LANG,
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
        # An old answer's cards are replayed as a note, so the model knows it
        # proposed a schedule earlier and whether the user applied it.
        (role, text + coach_tools.history_note(attachments))
        for role, text, attachments in db.execute(
            select(ChatMessage.role, ChatMessage.content, ChatMessage.attachments)
            .where(
                ChatMessage.chat_session_id == session.chat_session_id,
                # The question just saved is passed separately, not twice.
                ChatMessage.chat_message_id != question.chat_message_id,
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(HISTORY_LIMIT)
        ).all()
    ][::-1]  # oldest first, which is the order a conversation is read in

    # Rebuilt on every message rather than cached on the session: the user may
    # have finished a workout between two questions, and a coach quoting last
    # week's numbers at them is worse than one that is slightly slower.
    context = coach_context.build(db, session.user_id, language)
    toolbox = coach_tools.CoachToolbox(db, session.user_id, language)

    try:
        reply_text = await generate_reply(
            history, content, context=context, tools=toolbox
        )
    except AiCoachQuotaExceeded as exc:
        # 429, not 503: the user can act on this one by waiting, and the app
        # should say so rather than report a server fault.
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    except AiCoachUnavailable as exc:
        # 503, not 500: nothing is wrong with the request, and the client
        # should offer to try again rather than report a bug.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    answer = ChatMessage(
        chat_session_id=session.chat_session_id,
        role=ASSISTANT,
        # The model may let a card speak for itself; the message still needs
        # words, both for the bubble and for the CHECK on content.
        content=reply_text or toolbox.fallback_text(),
        attachments=toolbox.attachments or None,
    )
    db.add(answer)
    session.updated_at = func.now()
    db.commit()
    db.refresh(answer)
    db.refresh(session)

    user_read, answer_read = coach_tools.message_reads(db, [question, answer])
    return ChatReply(
        chat_session_id=session.chat_session_id,
        title=session.title,
        user_message=user_read,
        assistant_message=answer_read,
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
        messages=coach_tools.message_reads(db, list(messages)),
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
    # Only decides which language the exercise names in the context block are
    # written in; the coach answers in whatever language the question used.
    language: str = Query(DEFAULT_LANG, max_length=10),
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

    return await _answer(db, session, payload.content, language)


@router.post(
    "/ai-coach/sessions/{chat_session_id}/messages", response_model=ChatReply
)
async def send_chat_message(
    chat_session_id: int,
    payload: ChatMessageCreate,
    current_user: CurrentUserDep,
    db: DbSession,
    language: str = Query(DEFAULT_LANG, max_length=10),
) -> ChatReply:
    """Asks the coach something in an existing conversation."""
    session = _owned_session(db, chat_session_id, current_user.id)
    return await _answer(db, session, payload.content, language)


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


def _locked_proposal_message(
    db: Session, chat_message_id: int, user_id: uuid.UUID
) -> ChatMessage:
    message = coach_tools.owned_message_for_update(db, chat_message_id, user_id)
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
        )
    return message


@router.post(
    "/ai-coach/messages/{chat_message_id}/schedule/apply",
    response_model=ScheduleApplyResult,
)
def apply_schedule_proposal(
    chat_message_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ScheduleApplyResult:
    """Writes the schedule a coach answer proposed into the plan.

    The only way a proposal reaches the calendar. All dates or none: a rule
    that refuses one date rolls back the rest, and the proposal stays
    pending so the user can ask the coach for another.

    Dates already in the past are skipped and reported, not written.
    Applying twice is a 409 - the message row is locked while it is checked.
    """
    message = _locked_proposal_message(db, chat_message_id, current_user.id)
    try:
        plan_id = coach_tools.apply_proposal(db, message, current_user.id)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except coach_tools.ProposalConflict as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except TooManyRestDays as too_many:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"A plan cannot have more than {MAX_CONSECUTIVE_REST_DAYS} "
                f"rest days in a row (this would make {too_many.run_length})"
            ),
        ) from None

    db.commit()
    db.refresh(message)
    return ScheduleApplyResult(
        plan_id=plan_id, message=coach_tools.message_reads(db, [message])[0]
    )


@router.post(
    "/ai-coach/messages/{chat_message_id}/schedule/dismiss",
    response_model=ChatMessageRead,
)
def dismiss_schedule_proposal(
    chat_message_id: int,
    current_user: CurrentUserDep,
    db: DbSession,
) -> ChatMessageRead:
    """Marks a proposal as not wanted, so the card stops offering Apply."""
    message = _locked_proposal_message(db, chat_message_id, current_user.id)
    try:
        coach_tools.dismiss_proposal(message)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except coach_tools.ProposalConflict as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None

    db.commit()
    db.refresh(message)
    return coach_tools.message_reads(db, [message])[0]
