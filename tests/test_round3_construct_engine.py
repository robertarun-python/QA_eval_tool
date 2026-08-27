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


def test_decide_merges_declared_categories_into_state():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.updated_state["collection"] == "a list called salaries"
    assert decision.final_kind == "clarify"


def test_decide_asks_about_every_vague_category_in_one_bundle():
    category_status = {
        "collection": {"status": "declared", "value": "a list called salaries"},
        "iteration": {"status": "attempted_but_vague", "neutral_question": "How will it work through them?"},
        "comparison": {"status": "attempted_but_vague", "neutral_question": "What decides a match?"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.final_kind == "clarify"
    assert decision.ask_categories == ["iteration", "comparison"]


def test_decide_probes_earliest_not_addressed_when_nothing_attempted():
    category_status = {
        "collection": {"status": "not_addressed"},
        "iteration": {"status": "not_addressed"},
        "comparison": {"status": "not_addressed"},
    }
    decision = round3_construct_engine.decide(category_status, {}, REQUIRED)
    assert decision.ask_categories == ["collection"]


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
    assert decision.final_kind == "clarify"  # nothing in REQUIRED got resolved


def test_decide_honors_an_explicit_correction_to_an_already_declared_category():
    category_status = {"iteration": {"status": "declared", "value": "a while loop instead"}}
    decision = round3_construct_engine.decide(
        category_status,
        {"collection": "a list", "iteration": "a for loop", "comparison": "greater than"},
        REQUIRED,
    )
    assert decision.updated_state["iteration"] == "a while loop instead"
    assert decision.final_kind == "proceed"


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
