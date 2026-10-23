"""The AI coach's tools: what it may look up, suggest and propose.

Two halves. The first fakes Google's transport and pins the conversation
shape of function calling - that a tool result goes back with its call id,
that the model's own turn is replayed untouched, and that the last round
switches tools off so an answer always arrives. The second runs the tools
and the apply route against the database, because the rules that matter
there - ids from the catalog, the rest-day limit, apply-once - live in
queries and constraints, not in the model.

The model is never called for real; see test_ai_coach.py for why.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.endpoints import ai_coach
from app.services import coach_tools, gemini
from tests.conftest import API

SQUAT = 526  # Prisoner Squat, reps
BACK_SQUAT = 511  # Barbell Back Squat, reps
PLANK = 8  # duration
PUSH_UP = 216  # pushup_v1 on the local catalog


# ---- the function-calling loop ---------------------------------------------


class _FakeToolbox:
    declarations = [{"name": "search_exercises", "parameters": {"type": "object"}}]
    guide = "--- CÔNG CỤ ---"

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, args):
        self.calls.append((name, args))
        return {"results": [{"exercise_id": SQUAT, "name": "Prisoner Squat"}]}


def _call_part(name="search_exercises", args=None, call_id="call-1"):
    return {
        "functionCall": {"name": name, "args": args or {"query": "squat"}, "id": call_id},
        "thoughtSignature": "sig-abc",
    }


def _text(text):
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]}


def _calls(*parts):
    return {"candidates": [{"content": {"role": "model", "parts": list(parts)}}]}


@pytest.fixture
def google(monkeypatch):
    """Serves scripted Gemini bodies in order and records every request."""
    script: list[dict] = []
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=script[min(len(requests), len(script)) - 1])

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr(gemini.settings, "gemini_api_key", "test-key")
    return script, requests


def _ask(toolbox=None):
    return asyncio.run(gemini.generate_reply([], "Gợi ý bài squat", tools=toolbox))


def test_a_tool_result_goes_back_with_its_call(google):
    script, requests = google
    script += [_calls(_call_part()), _text("Thử Prisoner Squat nhé.")]
    toolbox = _FakeToolbox()

    assert _ask(toolbox) == "Thử Prisoner Squat nhé."
    assert toolbox.calls == [("search_exercises", {"query": "squat"})]

    assert len(requests) == 2
    second = requests[1]["contents"]
    # The model's turn is replayed exactly, thought signature included -
    # Gemini 3 refuses a function call history without it.
    assert second[-2]["parts"][0]["thoughtSignature"] == "sig-abc"
    response = second[-1]["parts"][0]["functionResponse"]
    assert response["id"] == "call-1"
    assert response["name"] == "search_exercises"
    assert response["response"]["results"][0]["exercise_id"] == SQUAT


def test_tools_and_their_guide_are_declared(google):
    script, requests = google
    script += [_text("Chào bạn")]
    _ask(_FakeToolbox())

    body = requests[0]
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "search_exercises"
    assert body["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert "--- CÔNG CỤ ---" in body["systemInstruction"]["parts"][0]["text"]


def test_the_last_round_has_to_answer(google):
    """A model that keeps calling tools is made to stop and write."""
    script, requests = google
    script += [_calls(_call_part())] * gemini.MAX_TOOL_ROUNDS + [_text("Xong")]

    assert _ask(_FakeToolbox()) == "Xong"
    assert len(requests) == gemini.MAX_TOOL_ROUNDS + 1
    modes = [r["toolConfig"]["functionCallingConfig"]["mode"] for r in requests]
    assert modes == ["AUTO"] * gemini.MAX_TOOL_ROUNDS + ["NONE"]


def test_an_empty_answer_after_tools_is_left_to_the_caller(google):
    script, _ = google
    script += [_calls(_call_part()), _text("")]
    assert _ask(_FakeToolbox()) == ""


def test_an_empty_answer_without_tools_is_still_an_error(google):
    script, _ = google
    script += [_text("")]
    with pytest.raises(gemini.AiCoachUnavailable):
        _ask(None)


# ---- the tools, against the database ---------------------------------------


def _user_id(client: TestClient, headers) -> uuid.UUID:
    return uuid.UUID(client.get(f"{API}/auth/me", headers=headers).json()["user"]["id"])


@pytest.fixture
def toolbox(client, owner, db_session):
    return coach_tools.CoachToolbox(db_session, _user_id(client, owner), "en")


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _squat_day(offset: int, **target) -> dict:
    return {
        "date": _day(offset),
        "exercises": [{"exercise_id": SQUAT, "sets": 3, "reps": 12, **target}],
    }


def test_search_finds_names_in_any_language(client, owner, db_session):
    # A Vietnamese user, an English search term.
    toolbox = coach_tools.CoachToolbox(db_session, _user_id(client, owner), "vi")
    found = toolbox.call("search_exercises", {"query": "prisoner", "limit": 50})
    assert found["results"][0]["exercise_id"] == SQUAT
    assert len(found["results"]) <= coach_tools.MAX_SEARCH_RESULTS


def test_search_puts_the_closest_name_first(toolbox):
    found = toolbox.call("search_exercises", {"query": "front squat"})
    assert found["results"][0]["exercise_id"] == 519  # not Smith Machine Front Squat


def test_search_can_ask_for_camera_countable_only(toolbox):
    found = toolbox.call("search_exercises", {"camera_countable": True, "limit": 10})
    assert found["results"]
    assert all(r["camera_countable"] for r in found["results"])


def test_an_invented_exercise_id_is_answered_not_kept(toolbox):
    result = toolbox.call("show_exercises", {"exercise_ids": [987654]})
    assert "error" in result and "search_exercises" in result["error"]
    assert toolbox.attachments == []


def test_shown_exercises_become_one_card_list(toolbox):
    toolbox.call("show_exercises", {"exercise_ids": [SQUAT], "reason": "Chân"})
    toolbox.call("show_exercises", {"exercise_ids": [SQUAT, PUSH_UP]})
    assert len(toolbox.attachments) == 1
    cards = toolbox.attachments[0]["exercises"]
    assert [c["exercise_id"] for c in cards] == [SQUAT, PUSH_UP]
    assert toolbox.attachments[0]["reason"] == "Chân"


@pytest.mark.parametrize(
    "days, complaint",
    [
        ([_squat_day(-2)], "outside"),
        ([_squat_day(1), _squat_day(1)], "twice"),
        ([{"date": _day(1), "exercises": [{"exercise_id": 987654, "sets": 3, "reps": 5}]}], "no such"),
        ([{"date": _day(1), "exercises": [{"exercise_id": PLANK, "sets": 3, "reps": 10}]}], "duration_seconds"),
        ([{"date": _day(i), "rest_day": True} for i in range(1, 5)], "rest days in a row"),
        ([{"date": _day(1), "rest_day": True, "exercises": [{"exercise_id": SQUAT, "sets": 1, "reps": 5}]}], "rest day"),
        ([_squat_day(1, weight_kg=5000)], "weight_kg"),
    ],
)
def test_a_bad_proposal_is_refused_with_a_reason(toolbox, days, complaint):
    result = toolbox.call("propose_workout_schedule", {"days": days})
    assert complaint in result.get("error", ""), result
    assert toolbox.attachments == []


def test_a_proposal_is_stored_not_written(toolbox, db_session):
    result = toolbox.call(
        "propose_workout_schedule",
        {"days": [_squat_day(1), {"date": _day(2), "rest_day": True}]},
    )
    assert "NOT saved" in result["status"]
    proposal = toolbox.attachments[0]
    assert proposal["status"] == "pending"
    assert proposal["creates_plan"] is True  # no active plan yet
    assert [d["rest_day"] for d in proposal["days"]] == [False, True]
    assert proposal["days"][0]["exercises"][0]["sets"] == 3


# ---- through the chat, then Apply ------------------------------------------


@pytest.fixture
def proposing_coach(monkeypatch):
    """A model that answers every question by proposing `next_days`."""
    state = {"days": [_squat_day(1)], "new_plan_name": None, "histories": []}

    async def fake_reply(history, question, context=None, tools=None):
        state["histories"].append(list(history))
        args = {"days": state["days"], "summary": "Tuần chân"}
        if state["new_plan_name"]:
            args["new_plan_name"] = state["new_plan_name"]
        result = tools.call("propose_workout_schedule", args)
        assert "error" not in result, result
        return ""  # the card speaks for itself - the fallback text fills in

    monkeypatch.setattr(ai_coach, "generate_reply", fake_reply)
    return state


def _propose(client, headers) -> dict:
    response = client.post(
        f"{API}/ai-coach/sessions", headers=headers, json={"content": "Lên lịch giúp mình"}
    )
    assert response.status_code == 201, response.text
    return response.json()["assistant_message"]


def _apply(client, headers, message_id):
    return client.post(f"{API}/ai-coach/messages/{message_id}/schedule/apply", headers=headers)


def _date(client, headers, plan_id, offset):
    return client.get(f"{API}/workout-plans/{plan_id}/dates/{_day(offset)}", headers=headers).json()


def test_applying_creates_an_active_plan_with_the_sets(client, owner, proposing_coach):
    proposing_coach["days"] = [_squat_day(1), {"date": _day(2), "rest_day": True}]
    message = _propose(client, owner)

    assert message["content"]  # fallback sentence, never empty
    card = message["attachments"][0]
    assert card["type"] == "schedule" and card["status"] == "pending"
    assert card["days"][0]["exercises"][0]["exercise_id"] == SQUAT

    applied = _apply(client, owner, message["chat_message_id"])
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["message"]["attachments"][0]["status"] == "applied"

    plan = client.get(f"{API}/workout-plans/{body['plan_id']}", headers=owner).json()
    assert plan["is_active"] is True

    workout = _date(client, owner, body["plan_id"], 1)
    [exercise] = workout["exercises"]
    assert exercise["exercise_id"] == SQUAT
    assert [s["target_reps"] for s in exercise["sets"]] == [12, 12, 12]
    assert _date(client, owner, body["plan_id"], 2)["is_rest_day"] is True


def test_a_proposal_applies_once(client, owner, proposing_coach):
    message = _propose(client, owner)
    assert _apply(client, owner, message["chat_message_id"]).status_code == 200
    again = _apply(client, owner, message["chat_message_id"])
    assert again.status_code == 409
    dismissed = client.post(
        f"{API}/ai-coach/messages/{message['chat_message_id']}/schedule/dismiss", headers=owner
    )
    assert dismissed.status_code == 409


def test_another_users_proposal_is_not_found(client, owner, stranger, proposing_coach):
    message = _propose(client, owner)
    assert _apply(client, stranger, message["chat_message_id"]).status_code == 404


def test_a_dismissed_proposal_cannot_be_applied(client, owner, proposing_coach):
    message = _propose(client, owner)
    dismissed = client.post(
        f"{API}/ai-coach/messages/{message['chat_message_id']}/schedule/dismiss", headers=owner
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["attachments"][0]["status"] == "dismissed"
    assert _apply(client, owner, message["chat_message_id"]).status_code == 409


def test_applying_into_the_active_plan_replaces_only_listed_dates(client, owner, proposing_coach):
    plan = client.post(f"{API}/workout-plans", headers=owner, json={"name": "Mine"}).json()
    client.put(f"{API}/workout-plans/{plan['plan_id']}/active", headers=owner, json={"is_active": True})
    for offset in (1, 3):
        client.put(
            f"{API}/workout-plans/{plan['plan_id']}/dates/{_day(offset)}/exercises",
            headers=owner,
            json={"exercise_ids": [PUSH_UP]},
        )

    proposing_coach["days"] = [_squat_day(1)]
    message = _propose(client, owner)
    card = message["attachments"][0]
    assert card["plan_id"] == plan["plan_id"] and card["creates_plan"] is False
    assert card["days"][0]["replaces_existing"] is True

    assert _apply(client, owner, message["chat_message_id"]).status_code == 200
    assert [e["exercise_id"] for e in _date(client, owner, plan["plan_id"], 1)["exercises"]] == [SQUAT]
    # Not in the proposal, so untouched.
    assert [e["exercise_id"] for e in _date(client, owner, plan["plan_id"], 3)["exercises"]] == [PUSH_UP]


def test_the_rest_day_limit_holds_against_the_existing_plan(client, owner, proposing_coach):
    plan = client.post(f"{API}/workout-plans", headers=owner, json={"name": "Mine"}).json()
    client.put(f"{API}/workout-plans/{plan['plan_id']}/active", headers=owner, json={"is_active": True})
    for offset in (2, 3, 4):
        response = client.put(
            f"{API}/workout-plans/{plan['plan_id']}/dates/{_day(offset)}/rest",
            headers=owner,
            json={"is_rest_day": True},
        )
        assert response.status_code == 200, response.text

    proposing_coach["days"] = [_squat_day(1, weight_kg=20), {"date": _day(5), "rest_day": True}]
    message = _propose(client, owner)
    refused = _apply(client, owner, message["chat_message_id"])
    assert refused.status_code == 409
    assert "rest days in a row" in refused.json()["detail"]

    # All or nothing: the workout on day 1 was rolled back with the rest day,
    # and the proposal is still there to try again.
    assert _date(client, owner, plan["plan_id"], 1) is None
    sessions = client.get(f"{API}/ai-coach/sessions", headers=owner).json()
    thread = client.get(f"{API}/ai-coach/sessions/{sessions[0]['chat_session_id']}", headers=owner).json()
    assert thread["messages"][-1]["attachments"][0]["status"] == "pending"


def test_past_dates_are_skipped_when_applied_late(client, owner, proposing_coach, monkeypatch):
    proposing_coach["days"] = [_squat_day(0), _squat_day(2)]
    message = _propose(client, owner)

    monkeypatch.setattr(coach_tools, "_today", lambda: date.today() + timedelta(days=1))
    applied = _apply(client, owner, message["chat_message_id"]).json()
    card = applied["message"]["attachments"][0]
    assert card["skipped_dates"] == [_day(0)]
    assert card["applied_dates"] == [_day(2)]
    assert _date(client, owner, applied["plan_id"], 0) is None


def test_a_new_plan_is_made_only_when_asked(client, owner, proposing_coach):
    plan = client.post(f"{API}/workout-plans", headers=owner, json={"name": "Mine"}).json()
    client.put(f"{API}/workout-plans/{plan['plan_id']}/active", headers=owner, json={"is_active": True})

    proposing_coach["new_plan_name"] = "Giáo án giảm mỡ"
    message = _propose(client, owner)
    applied = _apply(client, owner, message["chat_message_id"]).json()

    assert applied["plan_id"] != plan["plan_id"]
    assert client.get(f"{API}/workout-plans/{applied['plan_id']}", headers=owner).json()["is_active"]
    assert not client.get(f"{API}/workout-plans/{plan['plan_id']}", headers=owner).json()["is_active"]


def test_the_model_is_reminded_what_it_proposed(client, owner, proposing_coach):
    message = _propose(client, owner)
    _apply(client, owner, message["chat_message_id"])
    sessions = client.get(f"{API}/ai-coach/sessions", headers=owner).json()
    client.post(
        f"{API}/ai-coach/sessions/{sessions[0]['chat_session_id']}/messages",
        headers=owner,
        json={"content": "Còn tuần sau?"},
    )
    replayed = proposing_coach["histories"][-1][-1][1]
    assert "Đã đề xuất lịch" in replayed and "ĐÃ áp dụng" in replayed


def test_exercise_cards_come_back_with_pictures_signed(client, owner, monkeypatch):
    async def fake_reply(history, question, context=None, tools=None):
        tools.call("show_exercises", {"exercise_ids": [SQUAT]})
        return "Thử bài này"

    monkeypatch.setattr(ai_coach, "generate_reply", fake_reply)
    message = _propose(client, owner)
    [card] = message["attachments"]
    assert card["type"] == "exercises"
    assert card["exercises"][0]["exercise_id"] == SQUAT
    # Never stored: the column holds ids and names only.
    assert "icon_url" in card["exercises"][0]
