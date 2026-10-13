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
import base64
import json
import time
from typing import Protocol

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


class Toolbox(Protocol):
    """What the coach may call, and what happens when it does.

    Declared here so this file stays the only one that knows Gemini's shapes;
    `app/services/coach_tools.py` is the implementation, and knows nothing
    about `functionCall` parts.
    """

    @property
    def declarations(self) -> list[dict]: ...

    @property
    def guide(self) -> str: ...

    def call(self, name: str, args: dict) -> dict: ...


# How many times one answer may go back to the model with tool results before
# it has to write its reply. Every round is a separate request against the same
# daily allowance as a question, so this is a quota decision as much as a
# latency one: a schedule proposal needs search -> propose -> answer, three.
MAX_TOOL_ROUNDS = 4

# Once this much time has gone, the next request is the last: tools are
# switched off and the model has to answer with what it already has. Each call
# can still take _TIMEOUT_SECONDS on its own; this only stops a fifth one
# from starting late.
_TOOL_TIME_BUDGET_SECONDS = 35.0


async def generate_reply(
    history: list[tuple[str, str]],
    question: str,
    context: str | None = None,
    tools: Toolbox | None = None,
) -> str:
    """Asks the model, given `(role, content)` pairs oldest first.

    `history` is what the database holds for this conversation - not anything
    the client sent - and `question` is the message just saved. `context` is
    the user's training picture from `coach_context.build`.

    The context rides in the system instruction rather than as a first user
    message: it is an operator fact about who is asking, not something the
    user said, and putting it in the transcript invites the model to answer it
    or the user to argue with it.

    With `tools`, the model may ask for any of them; each call is run and its
    result sent back until the model answers in text. That can return an empty
    string when the tools themselves were the answer (a proposal card), so the
    caller decides what to say then - without tools, empty is still an error.
    """
    if not settings.can_use_ai_coach:
        raise AiCoachUnavailable(
            "AI coach is not configured on this server (GEMINI_API_KEY is unset)."
        )

    instruction = SYSTEM_PROMPT
    if tools is not None:
        instruction = f"{instruction}\n\n{tools.guide}"
    if context:
        instruction = f"{instruction}\n\n--- DỮ LIỆU NGƯỜI DÙNG ---\n{context}"

    contents = [
        {"role": _to_gemini_role(role), "parts": [{"text": content}]}
        for role, content in history
    ]
    contents.append({"role": "user", "parts": [{"text": question}]})

    started = time.monotonic()
    used_tools = False
    for round_number in range(MAX_TOOL_ROUNDS + 1):
        last = (
            tools is None
            or round_number == MAX_TOOL_ROUNDS
            or time.monotonic() - started > _TOOL_TIME_BUDGET_SECONDS
        )
        body = await _generate(
            _payload(contents, instruction, tools, tools_allowed=not last)
        )

        calls = _function_calls(body)
        if tools is None or not calls:
            return _extract_text(body, allow_empty=used_tools)

        # The model's turn goes back exactly as it came - Gemini 3 attaches a
        # thought signature to function-call parts, and a request that drops
        # it is refused.
        contents.append(body["candidates"][0]["content"])
        contents.append(
            {
                "role": "user",
                "parts": [_function_response(tools, call) for call in calls],
            }
        )
        used_tools = True

    raise AssertionError("unreachable")  # the last round always returns


def _payload(
    contents: list[dict],
    instruction: str,
    tools: Toolbox | None,
    *,
    tools_allowed: bool,
) -> dict:
    payload: dict = {
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

    if tools is not None:
        # Declared on every round, the last one included: the transcript by
        # then holds function calls, and those have to match a declaration.
        # The last round only switches calling off.
        payload["tools"] = [{"functionDeclarations": tools.declarations}]
        payload["toolConfig"] = {
            "functionCallingConfig": {"mode": "AUTO" if tools_allowed else "NONE"}
        }
    return payload


async def _generate(payload: dict) -> dict:
    """One generateContent request for the coach."""
    return await _post(
        settings.gemini_model,
        payload,
        timeout=_TIMEOUT_SECONDS,
        unavailable=AiCoachUnavailable,
        quota_exceeded=AiCoachQuotaExceeded,
        # The free tier is 20 requests a day, so this is the failure the user
        # will actually meet. Told apart from every other error because the
        # advice differs: wait, rather than report a bug.
        quota_message="Đã hết lượt hỏi AI Coach cho hôm nay. Thử lại sau nhé.",
        who="The coach",
    )


async def _post(
    model: str,
    payload: dict,
    *,
    timeout: float,
    unavailable: type[RuntimeError],
    quota_exceeded: type[RuntimeError],
    quota_message: str,
    who: str,
) -> dict:
    """One generateContent call, with every failure mapped to the caller's
    own exception classes - so the chat and the photo analysis each report
    themselves in their own words, and neither knows Gemini's error shapes."""
    url = _ENDPOINT.format(model=model)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await _post_with_retry(client, url, payload)
    except httpx.TimeoutException as exc:
        raise unavailable(f"{who} took too long to answer.") from exc
    except httpx.HTTPError as exc:
        raise unavailable(f"Could not reach {who.lower()}.") from exc

    if response.status_code == 429:
        raise quota_exceeded(quota_message)

    if response.status_code >= 400:
        # The provider's message can carry the API key back or name internal
        # details, so it is logged for us and not returned to the caller.
        raise unavailable(f"{who} refused the request (HTTP {response.status_code}).")

    return response.json()


def _function_calls(body: dict) -> list[dict]:
    """The `functionCall` parts of the first candidate, in order."""
    candidates = body.get("candidates") or []
    if not candidates:
        return []
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    return [part["functionCall"] for part in parts if "functionCall" in part]


def _function_response(tools: Toolbox, call: dict) -> dict:
    """Runs one requested tool and wraps its result the way Gemini expects."""
    result = tools.call(call.get("name", ""), call.get("args") or {})
    response: dict = {"name": call.get("name", ""), "response": result}
    if "id" in call:
        response["id"] = call["id"]
    return {"functionResponse": response}


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


def _extract_text(body: dict, *, allow_empty: bool = False) -> str:
    """Pulls the reply out of Gemini's response.

    Every level here is optional in practice: a prompt blocked by a safety
    filter comes back with no candidate at all, and a response cut off at the
    token limit can have a candidate with no parts. Each of those is a real
    answer to show the user, not an exception to swallow.

    `allow_empty` is for the answer after tools ran: a proposal card can be
    the whole answer, and the caller has a sentence for it.
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

    if not text and not allow_empty:
        raise AiCoachUnavailable("The coach returned an empty answer.")
    return text


# --------------------------------------------------------------------------
# Meal photos
# --------------------------------------------------------------------------

# What the model is asked to do with a plate. It names and portions; the
# nutrition it reports is only a fallback - wherever the catalog has the dish,
# the Institute's measured figures are used instead (services/food_photo.py).
FOOD_PHOTO_PROMPT = (
    "Bạn là chuyên gia dinh dưỡng Việt Nam. Hãy nhìn ảnh bữa ăn và liệt kê "
    "từng món ăn hoặc thực phẩm riêng biệt nhìn thấy được (tối đa 8). "
    "Với mỗi món:\n"
    "- name_vi: tên tiếng Việt thông dụng, như trong bảng thành phần thực "
    "phẩm Việt Nam (ví dụ \"Phở bò\", \"Cơm trắng\", \"Trứng gà luộc\").\n"
    "- search_terms: 1-3 tên khác để tra cứu, mỗi tên ít nhất 2 chữ: tên "
    "ngắn hơn (\"Phở bò tái\" -> \"Phở bò\") và tên đồng nghĩa theo cách gọi "
    "miền Bắc mà Viện Dinh dưỡng dùng (cơm trắng -> \"Cơm tẻ\", thịt heo -> "
    "\"Thịt lợn\"). Không dùng một chữ chung chung như \"Cơm\" hay \"Thịt\".\n"
    "- servings: số suất ăn tiêu chuẩn nhìn thấy (một bát/đĩa thông thường = 1).\n"
    "- grams: ước lượng khối lượng phần nhìn thấy, tính bằng gam.\n"
    "- energy_kcal, protein_g, fat_g, carb_g: ước lượng cho TOÀN BỘ phần nhìn "
    "thấy, không phải cho 100 g.\n"
    "- confidence: từ 0 đến 1, mức chắc chắn rằng tên món là đúng.\n"
    "Không liệt kê bát đĩa, đũa thìa, nước chấm chỉ có một chút. "
    "Không đoán món bị che khuất. Nếu ảnh không phải đồ ăn, trả is_food = false "
    "và items rỗng."
)

_NUMBER = {"type": "NUMBER"}

# Structured output rather than "please answer in JSON": the response is
# guaranteed to parse, so there is no fence-stripping or retry-on-garbage like
# the one-off OCR script needed.
FOOD_PHOTO_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_food": {"type": "BOOLEAN"},
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name_vi": {"type": "STRING"},
                    "search_terms": {"type": "ARRAY", "items": {"type": "STRING"}},
                    "servings": _NUMBER,
                    "grams": _NUMBER,
                    "energy_kcal": _NUMBER,
                    "protein_g": _NUMBER,
                    "fat_g": _NUMBER,
                    "carb_g": _NUMBER,
                    "confidence": _NUMBER,
                },
                "required": ["name_vi", "servings", "grams", "energy_kcal", "confidence"],
            },
        },
    },
    "required": ["is_food", "items"],
}

# A photo takes longer than a chat reply; the app waits up to 60 s for the
# whole request, upload included.
_PHOTO_TIMEOUT_SECONDS = 45.0


class FoodPhotoUnavailable(RuntimeError):
    """The photo could not be analysed right now, with a reason worth showing."""


class FoodPhotoQuotaExceeded(FoodPhotoUnavailable):
    """Google's allowance for the vision model is spent - 429, not 503."""


async def analyze_food_photo(image: bytes, mime_type: str) -> dict:
    """Ask the model what is on the plate. Returns the parsed JSON as-is;
    checking and clamping it is the caller's job, because the caller is the
    one that knows what the diary will accept."""
    if not settings.can_use_ai_coach:
        raise FoodPhotoUnavailable(
            "Photo analysis is not configured on this server (GEMINI_API_KEY is unset)."
        )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": FOOD_PHOTO_PROMPT},
                {"inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(image).decode(),
                }},
            ],
        }],
        "generationConfig": {
            # Zero temperature: the same plate should get the same answer
            # twice, and nothing here benefits from variety.
            "temperature": 0,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json",
            "responseSchema": FOOD_PHOTO_SCHEMA,
            # Same reason as the coach: thinking tokens count against the
            # output cap, and a cut-off JSON document is no document.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    body = await _post(
        settings.vision_model,
        payload,
        timeout=_PHOTO_TIMEOUT_SECONDS,
        unavailable=FoodPhotoUnavailable,
        quota_exceeded=FoodPhotoQuotaExceeded,
        quota_message=(
            "Hôm nay máy chủ đã dùng hết lượt phân tích ảnh. "
            "Bạn có thể thêm món thủ công hoặc thử lại sau."
        ),
        who="Photo analysis",
    )

    candidates = body.get("candidates") or []
    parts = ((candidates[0].get("content") or {}).get("parts")) if candidates else None
    text = "".join(part.get("text", "") for part in parts or []).strip()
    if not text:
        raise FoodPhotoUnavailable("Photo analysis returned nothing.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        # Possible only when the output was cut off at the token cap.
        raise FoodPhotoUnavailable("Photo analysis returned an unreadable answer.") from exc
    if not isinstance(parsed, dict):
        raise FoodPhotoUnavailable("Photo analysis returned an unreadable answer.")
    return parsed
