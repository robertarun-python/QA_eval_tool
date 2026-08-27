"""
Deterministic decision layer for Round 3's construct checklist - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure Python, no LLM calls of its own: the model classifies and drafts
(llm_service.round3_coding_turn), this decides what's actually missing
and what gets asked - never the model's own self-reported response_kind.
"""
from dataclasses import dataclass

from . import round3_constructs


@dataclass
class EngineDecision:
    final_kind: str  # "clarify" | "proceed" - "proceed" means the caller
                      # may use the model's own code_after as a code_edit.
    updated_state: dict
    ask_categories: list  # ordered; empty when final_kind == "proceed"


def merge_declared(category_status: dict, cumulative_state: dict, required_constructs: list) -> dict:
    """The declared-category merge step, factored out so a caller with
    no clarify/proceed decision to make (round3_syntax_fix - the code is
    the answer, there's nothing to ask about) can update state without
    going through decide()'s branching, which has no meaning there."""
    updated_state = dict(cumulative_state)
    for category, entry in category_status.items():
        if category not in required_constructs:
            continue  # off-topic mention - not tracked, not gated on
        if entry["status"] == "declared":
            updated_state[category] = entry["value"]
    return updated_state


def decide(category_status: dict, cumulative_state: dict, required_constructs: list) -> EngineDecision:
    updated_state = merge_declared(category_status, cumulative_state, required_constructs)
    attempted_vague = [
        category for category, entry in category_status.items()
        if category in required_constructs and entry["status"] == "attempted_but_vague"
    ]

    missing = [c for c in required_constructs if c not in updated_state]
    if not missing:
        return EngineDecision(final_kind="proceed", updated_state=updated_state, ask_categories=[])

    if attempted_vague:
        # This message tried to address more than one gap at once - ask
        # about all of them together, not one round trip each (see spec
        # §4, "bundled instructions"). Filtered to `missing` because the
        # model isn't guaranteed to only classify categories the prompt
        # actually showed it as open - an already-declared category
        # mistakenly re-classified as "attempted_but_vague" must never
        # get re-asked (the guardrail promises settled choices are final)
        # or crowd out the real gap.
        ask = [c for c in required_constructs if c in attempted_vague and c in missing]
        if ask:
            return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=ask)

    # Nothing was attempted this turn (or everything "attempted" was
    # actually already-declared noise, filtered above) - probe toward
    # the single earliest still-unaddressed category, in the scenario's
    # authored order.
    return EngineDecision(final_kind="clarify", updated_state=updated_state, ask_categories=[missing[0]])


def leaking_categories(ask_categories: list, category_status: dict, language: str) -> list:
    return [
        c for c in ask_categories
        if round3_constructs.contains_forbidden_vocab(category_status[c]["neutral_question"], c, language)
    ]


def assemble_message(ask_categories: list, category_status: dict, use_fallback_for: set) -> str:
    lines = []
    for c in ask_categories:
        if c in use_fallback_for:
            lines.append(round3_constructs.FALLBACK_QUESTIONS[c])
        else:
            lines.append(category_status[c]["neutral_question"])
    return "\n".join(lines)
