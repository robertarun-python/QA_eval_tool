"""
Round 2: values the candidate stated must reach the code exactly
(round2_automation_policy.changed_candidate_values). Run B's "use the same
user" turn came back with "Patient1" where the candidate had said
"Patient One" - nothing noticed. Offline: the model is faked.
"""
import json

from app.schemas import ASSESSOR_ONLY_CONTENT_KEYS
from app.services import llm_service
from app.services.round2_automation_policy import changed_candidate_values

STATED = [{"candidate_prompt": "Add patient1@test.com with password Pass@1234, name Patient One, status active to the database"}]


def test_run_b_same_user_drift_is_caught():
    code = 'db.add_user("patient1@test.com", "Pass@1234", "Patient1")\n'
    assert changed_candidate_values(code, [], STATED, "use the same user") == ["Patient One -> Patient1"]


def test_a_changed_email_is_caught():
    assert changed_candidate_values('login("patient2@test.com", "Pass@1234")', [], STATED, "x") == [
        "patient1@test.com -> patient2@test.com"]


def test_values_used_exactly_are_fine():
    code = 'db.add_user("patient1@test.com", "Pass@1234", "Patient One")\n'
    assert changed_candidate_values(code, [], STATED, "x") == []


def test_quoted_values_in_the_design_count():
    design = [{"title": "Welcome text", "expected_result": 'The header shows "Welcome back, Jordan"'}]
    assert changed_candidate_values('assert header == "Welcome, Jordan"', design, [], "x") == [
        "Welcome back, Jordan -> Welcome, Jordan"]


def test_not_flagged_environment_values_case_only_and_inconsistent_candidates():
    env = 'USERS = {"jordan.rivera@example.com": "Passw0rd!2026"}'
    assert changed_candidate_values('login("jordan.rivera@example.com")', [], STATED, "x", environment_code=env) == []
    assert changed_candidate_values('assert t == "Welcome, Jordan"', [], [{"candidate_prompt": "check 'welcome, jordan'"}], "x") == []
    # The candidate wrote both spellings - no single right one to hold the code to.
    both = [{"candidate_prompt": "password Wrong@999"}, {"candidate_prompt": "use password wrong@999"}]
    assert changed_candidate_values('login("x", "Wrong@999")', [], both, "x") == []


def test_a_value_ends_at_a_sentence_boundary():
    assert changed_candidate_values('login("Pass@123")', [], [], "Log in with password Pass@123. I expect the Home page") == []


# ---- in the Round 2 turn ----

def _edit(code):
    return json.dumps({"response_kind": "code_edit", "response_message": "Done.", "code_after": code})


def _turn(monkeypatch, replies, inject_flaw=False):
    prompts, queue = [], list(replies)

    def _fake(p, max_tokens=4096):
        prompts.append(p)
        return queue.pop(0)

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    result = llm_service.round2_automation_turn(
        language="python", selected_design=[{"title": "Login", "steps": "log in"}], environment_code="# env",
        current_code="", conversation_so_far=STATED, candidate_prompt="use the same user", inject_flaw=inject_flaw,
    )
    return result, prompts


DRIFTED = 'db.add_user("patient1@test.com", "Pass@1234", "Patient1")\n'
EXACT = 'db.add_user("patient1@test.com", "Pass@1234", "Patient One")\n'


def test_a_changed_value_is_regenerated(monkeypatch):
    result, prompts = _turn(monkeypatch, [_edit(DRIFTED), _edit(EXACT)])
    assert result["code_after"] == EXACT and len(prompts) == 2
    assert "Patient One -> Patient1" in prompts[1]
    assert "changed_values" not in result


def test_a_change_that_survives_the_retry_is_recorded_for_scoring_and_hidden_from_the_candidate(monkeypatch):
    result, _ = _turn(monkeypatch, [_edit(DRIFTED), _edit(DRIFTED)])
    assert result["changed_values"] == ["Patient One -> Patient1"]
    assert "changed_values" in ASSESSOR_ONLY_CONTENT_KEYS


def test_not_checked_on_the_planted_flaw_turn(monkeypatch):
    reply = json.dumps({"response_kind": "code_edit", "response_message": "Done.", "code_after": DRIFTED, "planted_flaw": "x"})
    result, prompts = _turn(monkeypatch, [reply], inject_flaw=True)
    assert len(prompts) == 1 and "changed_values" not in result
