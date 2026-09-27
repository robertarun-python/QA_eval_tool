"""
Round 2 conversations must move forward. Transcripts showed candidates
asked the same clarifying question up to 8 times - for things their own
Round 1 design already said - and one fixed sentence sent back whatever they
typed. Now: a complete design needs no questions, a test case is asked at
most once, a repeated message with nothing changed is answered without an
AI call, and one turn at a time per test case. Offline: the model is faked
and every call is counted.
"""
import json

import pytest

from app.routers import candidate as candidate_router
from app.services import llm_service

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_round2_automation import GATE_ROWS, PYTHON_ENV, _reach_automation_round, _select

CODE = PYTHON_ENV + "\nassert UI.login('jordan.rivera@example.com', 'Passw0rd!2026')\nprint('ok')\n"
EDIT = json.dumps({"response_kind": "code_edit", "response_message": "Encoded it.", "code_after": CODE})
INSUFFICIENT = json.dumps({"status": "insufficient", "question": "What exactly should prove this step worked?"})


def _model(monkeypatch, reply_for):
    """Fake model: reply_for(prompt) -> reply; returns the list of prompts it was sent."""
    calls = []

    def fake(prompt, max_tokens=4096):
        calls.append(prompt)
        return reply_for(prompt)

    monkeypatch.setattr(llm_service, "_call_claude", fake)
    return calls


def _is_gate(prompt):
    return "specification" in prompt.lower() or "sufficient" in prompt.lower() and "insufficient" in prompt.lower()


def _say(client, token, text, row=0):
    return client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": text, "row_index": row}, cookies=_auth(token))


def test_a_complete_design_goes_straight_to_code(client, monkeypatch):
    """The first stuck candidate typed "automate the testcase" and got the same question 5 times,
    though their design had the steps, data and expected result."""
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch)  # complete designs
    _select(client, cand, (0,))
    calls = _model(monkeypatch, lambda p: INSUFFICIENT if _is_gate(p) else EDIT)
    res = _say(client, cand, "automate the testcase")
    assert res.status_code == 201 and res.json()["response_kind"] == "code_edit"
    assert len(calls) == 1 and not _is_gate(calls[0])  # no clarifying call at all


def test_an_incomplete_design_gets_at_most_one_question(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch, r1_rows=GATE_ROWS)  # no test data
    _select(client, cand, (0,))
    calls = _model(monkeypatch, lambda p: INSUFFICIENT if _is_gate(p) else EDIT)
    first = _say(client, cand, "automate the testcase").json()
    assert first["response_kind"] == "clarify"
    # The model would ask again forever; the second message goes straight to code.
    second = _say(client, cand, "use owner jordan and amount 0.00").json()
    assert second["response_kind"] == "code_edit"
    assert sum(_is_gate(p) for p in calls) == 1


def test_an_empty_message_on_an_incomplete_design_names_the_missing_field(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch, r1_rows=GATE_ROWS)
    _select(client, cand, (0,))
    calls = _model(monkeypatch, lambda p: INSUFFICIENT)
    reply = _say(client, cand, "go").json()
    assert reply["response_message"] == "Which test data should the test use?" and calls == []


def test_the_same_message_again_with_nothing_changed_costs_no_ai_call(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch)
    _select(client, cand, (0,))
    calls = _model(monkeypatch, lambda p: EDIT)
    assert _say(client, cand, "Encode my test with my data").json()["response_kind"] == "code_edit"
    before = len(calls)
    again = _say(client, cand, "  encode my test with my data. ").json()
    assert again["response_message"] == candidate_router.REPEAT_MESSAGE and len(calls) == before

    # Something changed (the candidate ran the code): the same words are a new question.
    client.post("/candidate/round/2/auto/run", json={"code": CODE, "row_index": 0}, cookies=_auth(cand))
    _say(client, cand, "Encode my test with my data")
    assert len(calls) == before + 1


def test_one_turn_at_a_time_per_test_case(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    cand = _reach_automation_round(client, hr, monkeypatch)
    _select(client, cand, (0,))
    calls = _model(monkeypatch, lambda p: EDIT)
    import app.database as database_module
    from app.models import Submission
    db = database_module.SessionLocal()
    sid = db.query(Submission).filter(Submission.round_number == 2).one().id
    db.close()
    lock = candidate_router._turn_lock(sid, 0)
    lock.acquire()  # a first request still waiting on the model
    try:
        res = _say(client, cand, "Encode my test")
        assert res.status_code == 409 and "still answering" in res.json()["detail"] and calls == []
    finally:
        lock.release()
    assert _say(client, cand, "Encode my test").status_code == 201


@pytest.mark.parametrize("first, again", [
    ("write code to log in and check the dashboard", "Please write the code to log in and check the dashboard again."),
    ("Automate step 2", "can you automate step 2 now"),
    ("check the balance is 500", "Check that the balance is 500 please"),
])
def test_a_reworded_repeat_counts_as_the_same_message(first, again):
    assert candidate_router._same_message(first, again)


@pytest.mark.parametrize("first, again", [
    ("check the balance is 500", "check the balance is 600"),
    ("log in as 'asha@corp.test'", "log in as 'ben@corp.test'"),
    ("automate step 2", "automate step 3"),
    ("write code to log in", "write code to log out and check the login page"),
    ("check the error message", "check the stored record in the database instead"),
])
def test_a_changed_value_or_a_new_request_is_never_a_repeat(first, again):
    assert not candidate_router._same_message(first, again)
