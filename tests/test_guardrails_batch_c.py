"""
Batch C of the Sep 2026 assistant guardrail fixes - the remaining deferred
items, all checked without a real model call:

- the simulated R2 mode is retired (HR can't publish or go live with it)
- R3 flags technique choices the candidate never made, and replaces
  replies that describe a sort order the code doesn't produce
- R2 removes assertions nobody asked for and never reveals values that
  exist only in the environment
- R3 scoring evidence flags large pastes that follow tab switches
"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routers import hr as hr_router
from app.services import llm_service, round3_scope_guard as guard, scoring_service


# ---- Retired simulated R2 mode ---------------------------------------------

def _scenario(round_number=2, mode=None):
    return SimpleNamespace(round_number=round_number, config_json={"mode": mode} if mode else {})


def test_simulated_round2_mode_can_no_longer_go_live(monkeypatch):
    monkeypatch.setattr(settings, "legacy_simulated_round2_enabled", False)
    with pytest.raises(HTTPException) as err:
        hr_router._reject_retired_round2_mode(_scenario())
    assert err.value.status_code == 400 and "retired" in err.value.detail


@pytest.mark.parametrize("scenario", [
    _scenario(mode="ai_test_automation"), _scenario(mode="pilot_automation"), _scenario(round_number=1), _scenario(round_number=3),
])
def test_other_scenarios_are_unaffected(monkeypatch, scenario):
    monkeypatch.setattr(settings, "legacy_simulated_round2_enabled", False)
    hr_router._reject_retired_round2_mode(scenario)  # no exception


# ---- R3 technique choices ----------------------------------------------------

@pytest.mark.parametrize("instruction, before, after, flag", [
    ("remove the duplicates", "n = [3, 1]", "n = [3, 1]\nn = list(set(n))", "chose a set to remove duplicates"),
    ("sort it", "n = [3, 1]", "n = [3, 1]\nn.sort(reverse=True)", "chose descending order"),
    ("get the inputs from the user and store them in a list", "", "n = input().split()", "chose how the input values are separated"),
])
def test_r3_technique_the_candidate_did_not_choose_is_flagged(instruction, before, after, flag):
    assert flag in guard.unrequested_additions("python", before, after, instruction)


@pytest.mark.parametrize("instruction, before, after", [
    ("remove the duplicates using a set", "n = [3, 1]", "n = [3, 1]\nn = list(set(n))"),
    ("sort it in descending order", "n = [3, 1]", "n = [3, 1]\nn.sort(reverse=True)"),
    ("read the numbers from one line, space separated", "", "n = input().split()"),
    # reusing a technique already in the code isn't a new choice
    ("print the list", "n = list(set([1]))", "n = list(set([1]))\nprint(n)"),
])
def test_r3_technique_the_candidate_named_is_allowed(instruction, before, after):
    assert guard.unrequested_additions("python", before, after, instruction) == []


def test_r3_reply_claiming_the_wrong_sort_order_is_replaced(monkeypatch):
    """R3-33/34/38/40/43: descending sort described as 'largest at index -1'."""
    reply = json.dumps({"response_kind": "code_edit", "category_status": {},
                        "response_message": "Sorted the list so the largest number is at index -1.",
                        "code_after": "nums = [3, 1]\nnums.sort(reverse=True)"})
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: reply)
    result = llm_service.round3_coding_turn(
        scenario_description="d", language="python", conversation_so_far=[], current_code="nums = [3, 1]",
        candidate_prompt="sort the list in descending order with the largest at index -1", turn_number=1,
    )
    assert result["response_message"] == "Updated the code - added: nums.sort(reverse=True)"


def test_r3_accurate_reply_is_kept():
    assert not guard.misleading_order_claim("Sorted descending, largest first.", "x = [1]", "x = [1]\nx.sort(reverse=True)")
    assert not guard.misleading_order_claim("Sorted ascending.", "x = [1]", "x = [1]\nx.sort()")


# ---- R2 automation: unrequested assertions and environment leaks -------------

_DESIGN = [{"title": "Verify login", "steps": "Open the login page, log in, check the header.",
            "test_data": "username = jordan.rivera@example.com; password = Passw0rd!2026",
            "expected_result": "The dashboard header shows \"Welcome, Jordan\"."}]
_ENV = 'USERS = {"jordan.rivera@example.com": "Passw0rd!2026", "maria.lopez@example.com": "S3cret!pass"}'


def _code(extra=""):
    return ("def test_login():\n    page = UI.open(\"/login\")\n" + extra +
            "    ok = UI.login(\"jordan.rivera@example.com\", \"Passw0rd!2026\")\n"
            "    assert UI.header() == \"Welcome, Jordan\"\n")


def _reply(kind="code_edit", code=None, message="Done."):
    return json.dumps({"response_kind": kind, "response_message": message, "code_after": code})


def _auto_turn(monkeypatch, replies):
    prompts, queue = [], list(replies)

    def _fake(p, max_tokens=4096):
        prompts.append(p)
        return queue.pop(0)

    monkeypatch.setattr(llm_service, "_call_claude", _fake)
    result = llm_service.round4_auto_turn(
        language="python", selected_design=_DESIGN, environment_code=_ENV, current_code="",
        conversation_so_far=[], candidate_prompt="log in and check the header",
    )
    return result, prompts


def test_r2_unrequested_assertion_triggers_one_clean_regeneration(monkeypatch):
    extra = '    assert page["status"] == 200\n'
    result, prompts = _auto_turn(monkeypatch, [_reply(code=_code(extra)), _reply(code=_code())])
    assert result["code_after"] == _code() and len(prompts) == 2
    assert 'assert page["status"] == 200' in prompts[1]
    assert "unrequested_checks" not in result


def test_r2_unrequested_assertion_is_removed_if_the_retry_keeps_it(monkeypatch):
    extra = '    assert page["status"] == 200\n'
    result, _ = _auto_turn(monkeypatch, [_reply(code=_code(extra)), _reply(code=_code(extra))])
    assert 'status"] == 200' not in result["code_after"]
    assert 'assert UI.header() == "Welcome, Jordan"' in result["code_after"]  # the requested check stays


def test_r2_assertions_from_the_candidates_design_are_not_touched(monkeypatch):
    result, prompts = _auto_turn(monkeypatch, [_reply(code=_code())])
    assert result["code_after"] == _code() and len(prompts) == 1


def test_r2_reply_revealing_environment_only_values_is_redacted(monkeypatch):
    leak = "The environment only has maria.lopez@example.com with password S3cret!pass."
    result, prompts = _auto_turn(monkeypatch, [_reply("explain", message=leak), _reply("explain", message=leak)])
    assert len(prompts) == 2
    assert "maria.lopez@example.com" not in result["response_message"]
    assert "S3cret!pass" not in result["response_message"]


def test_r2_reply_repeating_the_candidates_own_test_data_is_fine(monkeypatch):
    msg = "Login with jordan.rivera@example.com failed - the run returned False."
    result, prompts = _auto_turn(monkeypatch, [_reply("explain", message=msg)])
    assert result["response_message"] == msg and len(prompts) == 1


# ---- R3 large pastes -----------------------------------------------------------

def _t(kind, code, at):
    return SimpleNamespace(response_kind=kind, code_after=code, created_at=at)


def test_large_paste_after_tab_switches_is_flagged():
    solution = "\n".join(f"line_{i} = {i}" for i in range(15))
    turns = [_t("code_edit", "a = 1", datetime(2026, 9, 23, 10, 0)), _t("direct_edit", solution, datetime(2026, 9, 23, 10, 10))]
    payload = [{}, {}]
    scoring_service._flag_large_pastes(turns, payload, ["2026-09-23T10:07:00", "2026-09-23T09:00:00"])
    assert payload[1]["large_paste"] == {"lines_added": 15, "tab_switches_in_previous_5_minutes": 1}
    assert "large_paste" not in payload[0]


def test_small_direct_edits_are_not_flagged():
    turns = [_t("direct_edit", "a = 1\nb = 2", datetime(2026, 9, 23, 10, 0))]
    payload = [{}]
    scoring_service._flag_large_pastes(turns, payload, [])
    assert payload == [{}]
