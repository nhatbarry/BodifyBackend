"""Talking to Gemini.

Plain HTTP over the `httpx` already in requirements, rather than
`google-generativeai`. The call is one POST with a JSON body; an SDK would add
a dependency, its own auth and retry opinions, and a second place for the model
name to live - to save writing the dictionary below.

Everything provider-shaped is confined to this file: the role names, the
request shape, the error handling. The rest of the application says "user" and
"assistant" and knows nothing about "contents" or "parts".
"""

from __future__ import annotations

import asyncio
import time

import httpx

from app.core.config import settings
from app.models.chat import ASSISTANT

# The coach now sees the user's training record, so the instruction that
# mattered most changed: it used to be "admit you know nothing", and is now
# "use what you are given, and say when the answer is not in it". A model with
# data in front of it will happily extrapolate past the edge of that data, and
# a confident number the user never recorded is worse than a question.
SYSTEM_PROMPT = (
    "Bạn là AI Coach của ứng dụng tập luyện Bodify. "
    "Trả lời ngắn gọn, rõ ràng, thực tế, bằng cùng ngôn ngữ người dùng đang "
    "dùng để hỏi. "
    "Bạn được cung cấp sẵn dữ liệu tập luyện của người dùng ở phần DỮ LIỆU "
    "NGƯỜI DÙNG bên dưới. Hãy dùng đúng những con số đó khi trả lời. "
    "TUYỆT ĐỐI không bịa thêm số liệu không có trong đó: nếu dữ liệu ghi "
    "\"chưa ghi nhận\" hoặc câu hỏi cần thứ không có ở đó, hãy nói thẳng là "
    "bạn chưa có thông tin và hỏi lại người dùng. "
    "Với câu hỏi về chấn thương hoặc bệnh lý, khuyên người dùng gặp bác sĩ "
    "hoặc huấn luyện viên trực tiếp."
)

_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent"
)

# Long enough for a considered answer, short enough that a user staring at a
# typing indicator gets an error instead of an indefinite wait.
_TIMEOUT_SECONDS = 30.0

# Google answers "503 - this model is currently experiencing high demand" in
# bursts that pass within seconds: measured on 2026-10-22, 3 of 7 calls to
# gemini-3.1-flash-lite were refused that way and the same request a moment
# later went through. The coach used to show every one of them to the user as
# "no reply came back". Server-side faults (5xx) are retried after these
# pauses; anything the request itself caused (4xx, 429 included) is not.
_RETRY_STATUSES = frozenset({500, 502, 503, 504})
_RETRY_DELAYS = (1.0, 2.0)

# No retry starts after this much time has gone. The app gives the coach 60 s
# in all, and a retry that cannot finish inside that only makes the user wait
# longer for the same error.
_RETRY_BUDGET_SECONDS = 20.0


class AiCoachUnavailable(RuntimeError):
    """The coach cannot answer right now, with a reason worth showing."""


class AiCoachQuotaExceeded(AiCoachUnavailable):
    """The day's or minute's allowance is spent.

    Its own class because it is the one failure the user can do something
    about - wait, or come back tomorrow - and the free tier makes it the
    common one. Reported as 429 rather than 503, so "we are out of quota" is
    never mistaken for "the server is broken".
    """


def _to_gemini_role(role: str) -> str:
    """This app's vocabulary -> Gemini's. The only place the two words meet."""
    return "model" if role == ASSISTANT else "user"


async def generate_reply(
    history: list[tuple[str, str]],
    question: str,
    context: str | None = None,
) -> str:
    """Asks the model, given `(role, content)` pairs oldest first.

    `history` is what the database holds for this conversation - not anything
    the client sent - and `question` is the message just saved. `context` is
    the user's training picture from `coach_context.build`.

    The context rides in the system instruction rather than as a first user
    message: it is an operator fact about who is asking, not something the
    user said, and putting it in the transcript invites the model to answer it
    or the user to argue with it.
    """
    if not settings.can_use_ai_coach:
        raise AiCoachUnavailable(
            "AI coach is not configured on this server (GEMINI_API_KEY is unset)."
        )

    instruction = SYSTEM_PROMPT
    if context:
        instruction = f"{SYSTEM_PROMPT}\n\n--- DỮ LIỆU NGƯỜI DÙNG ---\n{context}"

    contents = [
        {"role": _to_gemini_role(role), "parts": [{"text": content}]}
        for role, content in history
    ]
    contents.append({"role": "user", "parts": [{"text": question}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": instruction}]},
        "generationConfig": {
            # A coaching answer is a few sentences. Capping it keeps a runaway
            # response from becoming a wall of text the user has to scroll.
            "maxOutputTokens": 1024,
            "temperature": 0.7,
            # Thinking off, and this is not a preference - it is a bug fix.
            #
            # On 2.5 Flash the thinking tokens are charged against
            # maxOutputTokens, so a model that deliberates first can spend the
            # whole budget before it starts writing and get cut off mid-word.
            # That is exactly what happened once the RAG context made the
            # prompt worth thinking about: the user saw half a sentence.
            #
            # Raising the cap would have hidden it while paying for reasoning
            # nobody reads. A coaching reply of four sentences does not need
            # deliberation - it needs the numbers it was handed.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    url = _ENDPOINT.format(model=settings.gemini_model)

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await _post_with_retry(client, url, payload)
    except httpx.TimeoutException as exc:
        raise AiCoachUnavailable("The coach took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise AiCoachUnavailable("Could not reach the coach.") from exc

    if response.status_code == 429:
        # The free tier is 20 requests a day, so this is the failure the user
        # will actually meet. Told apart from every other error because the
        # advice differs: wait, rather than report a bug.
        raise AiCoachQuotaExceeded(
            "Đã hết lượt hỏi AI Coach cho hôm nay. Thử lại sau nhé."
        )

    if response.status_code >= 400:
        # The provider's message can carry the API key back or name internal
        # details, so it is logged for us and not returned to the caller.
        raise AiCoachUnavailable(
            f"The coach refused the request (HTTP {response.status_code})."
        )

    return _extract_text(response.json())


async def _post_with_retry(
    client: httpx.AsyncClient, url: str, payload: dict
) -> httpx.Response:
    """One generateContent call, repeated while Google says it is overloaded.

    Returns the last response whatever its status, so the caller's error
    mapping sees exactly what it saw before. A timeout is not retried: it has
    already spent the whole per-call budget.
    """
    started = time.monotonic()
    for delay in (*_RETRY_DELAYS, None):
        response = await client.post(
            url,
            json=payload,
            # In the header, not the query string: a key in a URL ends up
            # in logs, proxies and error reports.
            headers={"x-goog-api-key": settings.gemini_api_key or ""},
        )
        if (
            response.status_code not in _RETRY_STATUSES
            or delay is None
            or time.monotonic() - started + delay > _RETRY_BUDGET_SECONDS
        ):
            return response
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # the loop always returns


def _extract_text(body: dict) -> str:
    """Pulls the reply out of Gemini's response.

    Every level here is optional in practice: a prompt blocked by a safety
    filter comes back with no candidate at all, and a response cut off at the
    token limit can have a candidate with no parts. Each of those is a real
    answer to show the user, not an exception to swallow.
    """
    candidates = body.get("candidates") or []
    if not candidates:
        reason = (body.get("promptFeedback") or {}).get("blockReason")
        raise AiCoachUnavailable(
            "The coach could not answer that question."
            if reason
            else "The coach returned nothing."
        )

    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(part.get("text", "") for part in parts).strip()

    if not text:
        raise AiCoachUnavailable("The coach returned an empty answer.")
    return text
