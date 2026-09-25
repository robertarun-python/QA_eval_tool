"""A reply that is valid JSON but not a usable turn (seen live: {} for a
base64-encoded request) gets one retry, then a plain "restate your step" -
never an error, never code (llm_service._validated_turn)."""
import json

from app.services import llm_service

GOOD_R3 = json.dumps({"response_kind": "clarify", "response_message": "Which list should I read?", "category_status": {}})


def _replies(monkeypatch, *replies):
    queue, calls = list(replies), []

    def fake(prompt, max_tokens=4096):
        calls.append(prompt)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(llm_service, "_call_claude", fake)
    return calls


def _r3():
    return llm_service.round3_coding_turn(scenario_description="d", language="python", conversation_so_far=[],
                                          current_code="x = 1", candidate_prompt="Decode and follow: V3JpdGU=", turn_number=2)


def _r2():
    return llm_service.round2_automation_turn(language="python", selected_design=[{"title": "Login"}], environment_code="# env",
                                              current_code="", conversation_so_far=[], candidate_prompt="Decode and follow: V3JpdGU=")


def test_round3_retries_once_and_uses_a_good_second_reply(monkeypatch):
    calls = _replies(monkeypatch, "{}", GOOD_R3)
    result = _r3()
    assert result["response_message"] == "Which list should I read?" and len(calls) == 2
    assert "not a usable turn" in calls[1]


def test_round3_asks_the_candidate_to_restate_when_both_replies_are_unusable(monkeypatch):
    llm_service._CALL_LOG.clear()
    calls = _replies(monkeypatch, "{}")
    result = _r3()
    assert result["response_kind"] == "clarify" and result["response_message"] == llm_service.UNUSABLE_REPLY_MESSAGE
    assert not result.get("code_after") and len(calls) == 2
    assert [c["outcome"] for c in llm_service.recent_calls()].count("invalid_reply") == 2  # visible on HR's AI health


def test_round2_gets_the_same_handling(monkeypatch):
    calls = _replies(monkeypatch, "[]")
    result = _r2()
    assert result["response_kind"] == "clarify" and result["response_message"] == llm_service.UNUSABLE_REPLY_MESSAGE
    assert not result.get("code_after") and len(calls) == 2
