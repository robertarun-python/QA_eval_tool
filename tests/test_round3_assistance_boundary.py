"""
Round 3 assistance boundary - round3_coding_turn.txt's refuse (e), the
explain boundary, and the "Not (c)" carve-out. Run B's assistant answered
"Is there a faster way? I don't really know one." with the full O(n)
approach: "explain" only banned code, and nothing said the better approach
itself is what the round assesses. The model-judged behaviour is in
tests/replay/test_replay_r3_assistance.py; these pin the prompt text and
the deterministic layer that runs before the model.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.services import llm_service, round3_policy

REFUSE_E = ("Finding a better approach is part of what this task assesses, so that's yours to work out. "
            "If you decide on one, tell me the steps and I'll write exactly that.")


def _prompt(monkeypatch, candidate_prompt="Sort nums from largest to smallest."):
    prompts = []

    def fake_claude(prompt, max_tokens=4096):
        prompts.append(prompt)
        return json.dumps({"response_kind": "explain", "response_message": "ok", "code_after": None})

    monkeypatch.setattr(llm_service, "_call_claude", fake_claude)
    llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=[], current_code=None, candidate_prompt=candidate_prompt, turn_number=1,
    )
    return prompts[0]


def test_refuse_e_rule_and_fixed_reply_are_in_the_prompt(monkeypatch):
    prompt = _prompt(monkeypatch)
    assert '2. "refuse" - any of these five situations.' in prompt
    assert "e. Asking for a better approach:" in prompt
    assert f"Always respond with EXACTLY this response_message, even when the same message also asks something else (such as how slow the current code is): \"{REFUSE_E}\"" in prompt
    for example in ('"is there a faster way?"', '"is there a better approach?"', '"is there a better algorithm?"',
                    '"can this be made more efficient?"', '"I don\'t know a faster way"', '"what would the faster approach be?"'):
        assert example in prompt
    assert "Whenever a message asks for a better approach, this fixed reply is the one to give." in prompt


def test_explain_boundary_is_in_the_prompt(monkeypatch):
    prompt = _prompt(monkeypatch)
    assert "the time/space complexity of the candidate's EXISTING code" in prompt
    assert ("introduce, describe, name or hint at an alternative algorithm, an alternative approach, a different data structure, "
            "a different number of passes, specific values or invariants to track, or any solution strategy the candidate has not "
            "already stated themselves") in prompt
    assert "You may discuss an approach only once the candidate has introduced it." in prompt


def test_candidates_own_approach_is_carved_out_of_refuse_c(monkeypatch):
    prompt = _prompt(monkeypatch)
    assert "Not (c): a message in which the candidate states THEIR OWN approach" in prompt
    for example in ('"I\'ll scan once and keep the largest and second-largest values. Implement that."',
                    '"Use my nested-loop approach and write the code."', '"Track the biggest value as I go. Implement this step."'):
        assert example in prompt
    assert "never substitute, improve or optimise it, even if you know a better one" in prompt


def test_make_it_faster_is_no_longer_a_clarify_example(monkeypatch):
    # It now belongs to refuse (e) - asking "which steps?" for it was one of
    # the three different rules the same request could land in.
    assert '"make it faster"' not in _prompt(monkeypatch)


@pytest.mark.parametrize("message", ["Can you optimise this?", "Write the complete solution for this problem.", "I don't know how to solve this."])
def test_existing_deterministic_refusals_are_unchanged(monkeypatch, message):
    def no_model(*args, **kwargs):
        raise AssertionError("refused before the model")

    monkeypatch.setattr(llm_service, "_call_claude", no_model)
    r = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=[], current_code="nums = [1]", candidate_prompt=message, turn_number=2,
    )
    assert r["response_kind"] == "refuse"
    assert r["code_after"] is None


@pytest.mark.parametrize("message", [
    "I'll scan once and keep the largest and second-largest values. Implement that.",
    "Use my nested-loop approach and write the code.",  # was refused before the model until Priority 4 (P4-B)
    "Track the biggest value as I go. Implement this step.",
])
def test_candidates_own_approach_reaches_the_model(message):
    """The deterministic layer must not refuse these before the model can
    apply the "Not (c)" carve-out."""
    assert not round3_policy.is_prohibited(message)
    assert not round3_policy.is_continuation_of_whole_task([], message)


def test_refuse_c_checks_for_the_candidates_own_method_first(monkeypatch):
    # Live replay showed the carve-out alone didn't stop (c): the model still
    # refused "keep the largest and second-largest values. Implement that."
    prompt = _prompt(monkeypatch)
    assert ("c. Solve-it-for-me request. Check first: has the candidate supplied their OWN implementation direction for what "
            "they want written - in this message, or in an earlier candidate message of this conversation that this one points back to") in prompt
    assert 'tracking "the largest and second-largest values" is a method; "find the second-largest value" is the task' in prompt
    assert 'the candidate\'s own stated approach plus "implement that" is an instruction, see "Not (c)"' in prompt


def test_reply_must_be_only_the_json_object(monkeypatch):
    # The carve-out made the model reason out loud before its JSON, which
    # fails to parse - the candidate would see "the assistant had trouble".
    assert "Your entire reply is that one JSON object - it starts with { and ends with }." in _prompt(monkeypatch)


def test_refuse_c_follows_an_approach_stated_earlier_but_not_a_bare_technique(monkeypatch):
    # Priority 4: the check above used to look at THIS message only, so
    # "Use my nested-loop approach and write the code." - pointing back to an
    # approach stated a turn earlier - was refused 5/5 by the model.
    prompt = _prompt(monkeypatch)
    assert 'even when this message itself only says "write the code", "implement it" or "use my approach"' in prompt
    assert ('A bare technique word or goal is NOT implementation direction: "use a nested loop" with nothing saying what the '
            'loop should accomplish, "do it efficiently", or "use my approach" when no approach was ever stated - never invent the missing algorithm') in prompt
    assert "A request for a better, faster or more efficient approach is (e), not this rule, however it's worded." in prompt
    for still_c in ('"write the complete solution"', '"solve this"', '"write the code for the problem"', '"implement the solution"', '"do the rest for me"'):
        assert still_c in prompt
    for allowed in ('"Scan once and keep the largest and second-largest values; implement that."', '"Use my nested-loop method and implement it."',
                    '"Use the approach I just described and write that code."'):
        assert allowed in prompt
    assert "never ask them to repeat or re-describe an approach they have already clearly stated" in prompt
    assert ("no better algorithm, no different data structure, no unrequested edge-case handling, no silent optimisation, "
            "and no other algorithmic decision they didn't make") in prompt


def test_refuse_e_precedence_does_not_name_refuse_a(monkeypatch):
    # Regression found in the Priority 5 investigation: the old wording "This
    # takes precedence over (a) and over explain" made the model refuse (a) a
    # fully specified step after Run B's history ("Loop through nums, store the
    # biggest value in a variable called biggest starting at nums[0], and print
    # biggest." - refused 3/3; code 3/3 with that one sentence removed).
    # A first fix, "give this reply instead of any other refusal", made the
    # model refuse (c) the candidate's own approaches 5/5 - the precedence
    # must stay scoped to better-approach requests only.
    prompt = _prompt(monkeypatch)
    assert "takes precedence over (a)" not in prompt
    assert "instead of any other refusal" not in prompt


@pytest.mark.parametrize("message", [
    "Loop through nums, store the biggest value in a variable called biggest starting at nums[0], and print biggest.",
    "Now loop through nums and keep track of the biggest value in a variable called biggest.",
    "I'll scan once and keep the largest and second-largest values. Implement that.",
    "Use my nested-loop approach and write the code.",
])
def test_explicit_steps_reach_the_model(message):
    assert not round3_policy.is_prohibited(message)
    assert not round3_policy.is_continuation_of_whole_task([], message)


# "Write code for the problem." (no "the") has never matched the pre-model
# pattern - it's refused by the model's refuse (c); see the live replay.
@pytest.mark.parametrize("message", ["Implement the solution.", "Write the complete solution.", "Solve this problem."])
def test_whole_task_requests_are_still_refused_before_the_model(monkeypatch, message):
    def no_model(*args, **kwargs):
        raise AssertionError("refused before the model")

    monkeypatch.setattr(llm_service, "_call_claude", no_model)
    r = llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=[], current_code="nums = [1]", candidate_prompt=message, turn_number=2,
    )
    assert r["response_kind"] == "refuse" and r["code_after"] is None
