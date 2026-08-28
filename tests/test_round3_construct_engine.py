"""
The deterministic decision layer for Round 3's construct checklist - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure Python, no LLM calls: given what the model classified this turn
(category_status) and what's already known (cumulative_state), decides
what's actually missing and what to ask.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import round3_construct_engine, round3_constructs

REQUIRED = ["collection", "iteration", "comparison"]


def test_merge_declared_folds_declared_categories_into_state():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "not_addressed"},
    }
    updated = round3_construct_engine.merge_declared(category_status, {}, REQUIRED)
    assert updated["collection"] == "a list called salaries"
    assert "iteration" not in updated


def test_merge_declared_ignores_categories_outside_the_required_list():
    category_status = {"recursion": {"status": "declared", "value": "yes"}}
    updated = round3_construct_engine.merge_declared(category_status, {}, REQUIRED)
    assert "recursion" not in updated


def test_merge_declared_overwrites_a_stale_value():
    category_status = {"iteration": {"status": "declared", "value": "a while loop, per the pasted code"}}
    updated = round3_construct_engine.merge_declared(category_status, {"iteration": "a for loop"}, REQUIRED)
    assert updated["iteration"] == "a while loop, per the pasted code"


def test_decide_merges_declared_categories_into_state():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.updated_state["collection"] == "a list called salaries"
    # iteration/comparison are merely not_addressed (this instruction
    # never touched them), not attempted-and-left-vague - that must
    # never withhold code for what this instruction DID specify. They
    # simply stay open for whichever later instruction addresses them.
    assert decision.final_kind == "proceed"


def test_decide_asks_about_every_vague_category_in_one_bundle():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them?"},
        "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.final_kind == "clarify"
    assert decision.ask_categories == ["iteration", "comparison"]


def test_decide_proceeds_when_nothing_was_attempted_this_turn():
    # An instruction unrelated to every open category (e.g. it only
    # covers input/validation while collection/iteration/comparison are
    # still open) must never be forced into a clarify about something it
    # never touched - no proactive probing toward "the next" open
    # category. Those stay silently open for a later instruction, same
    # as the direct-edit path's no-gap-flagging behavior.
    category_status = {
        "collection": {"status": "not_addressed"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.final_kind == "proceed"
    assert decision.ask_categories == []


def test_decide_proceeds_once_every_required_category_is_declared():
    category_status = {"comparison": {"status": "declared", "value": "greater than"}}
    decision = round3_construct_engine.decide(
        category_status,
        {"collection": "a list", "iteration": "a for loop"},
        REQUIRED,
    )
    assert decision.final_kind == "proceed"
    assert decision.ask_categories == []


def test_decide_ignores_categories_outside_the_required_list():
    category_status = {"recursion": {"status": "declared", "value": "yes"}}
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert "recursion" not in decision.updated_state
    # Nothing in REQUIRED was left attempted-but-vague (the off-topic
    # "recursion" mention is correctly ignored) - proceeds, and every
    # REQUIRED category simply stays open for later.
    assert decision.final_kind == "proceed"


def test_decide_honors_an_explicit_correction_to_an_already_declared_category():
    category_status = {"iteration": {"status": "declared", "value": "a while loop instead"}}
    decision = round3_construct_engine.decide(
        category_status,
        {"collection": "a list", "iteration": "a for loop", "comparison": "greater than"},
        REQUIRED,
    )
    assert decision.updated_state["iteration"] == "a while loop instead"
    assert decision.final_kind == "proceed"


def test_decide_ignores_a_stray_attempted_vague_status_on_an_already_declared_category():
    # A model that misclassifies an already-settled category as still
    # "attempted_but_vague" (schema-legal, but not what the prompt asks
    # for) must never get re-asked about it. And "iteration" being
    # merely not_addressed this turn must never force a question either
    # - it just stays open for a later instruction, same as any other
    # untouched category.
    category_status = {
        "collection": {"status": "attempted_but_vague", "neutral_question": "How should this be represented?"},
        "iteration": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(
        category_status, {"collection": "a list"}, REQUIRED,
    )
    assert decision.final_kind == "proceed"
    assert decision.ask_categories == []


def test_leaking_categories_flags_a_question_that_names_the_construct():
    category_status = {"iteration": {"status": "attempted_but_vague", "neutral_question": "Should this be a for loop?"}}
    leaked = round3_construct_engine.leaking_categories(["iteration"], category_status, "python")
    assert leaked == ["iteration"]


def test_leaking_categories_passes_a_clean_question():
    category_status = {"iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them, one at a time?"}}
    leaked = round3_construct_engine.leaking_categories(["iteration"], category_status, "python")
    assert leaked == []


def test_assemble_message_uses_fallback_for_flagged_categories():
    category_status = {
        "iteration": {"status": "attempted_but_vague", "neutral_question": "Should this be a for loop?"},
        "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
    }
    message = round3_construct_engine.assemble_message(
        ["iteration", "comparison"], category_status, use_fallback_for={"iteration"}
    )
    assert round3_constructs.FALLBACK_QUESTIONS["iteration"] in message
    assert "What decides a match?" in message
    assert "Should this be a for loop?" not in message
