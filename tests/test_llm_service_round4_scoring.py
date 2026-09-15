"""
Direct contract tests for llm_service.score_round4_conversation - not
the deterministic evidence audit itself (see test_round4_evidence_audit.py),
but the boundary right before it: does a real (mocked) Claude response
actually get forced through that audit, or can a malformed response
bypass it? No live Claude call - _call_claude is monkeypatched.
"""
import pytest

from app.services import llm_service


def _score(round4_evidence=None, round1_reference_context=None, assistance_pct=60):
    return llm_service.score_round4_conversation(
        round4_evidence=round4_evidence or {"test_cases": []},
        round1_reference_context=round1_reference_context or {"scenario_title": "", "scenario_description": "", "submitted_rows": []},
        assistance_pct=assistance_pct,
    )


def test_a_response_missing_findings_is_rejected_not_silently_passed_through(monkeypatch):
    """The prompt this function sends always asks for "findings" - a
    real response reverting to the old flat "misses" shape must not be
    treated as a legacy fallback (that would let its final_score bypass
    round4_evidence_audit entirely - see scoring_service._round4_findings_to_misses's
    docstring). It must raise, the same as any other malformed scoring response."""
    monkeypatch.setattr(
        llm_service, "_call_claude",
        lambda prompt, max_tokens=4096: '{"coverage_score": 90, "misses": [], "final_score": 95, "feedback_text": "great job"}',
    )
    with pytest.raises(ValueError, match="findings"):
        _score()


def test_a_response_with_an_empty_findings_list_is_accepted(monkeypatch):
    """The new shape with zero findings (nothing to deduct) is valid and
    must not be confused with the missing-key case above."""
    monkeypatch.setattr(
        llm_service, "_call_claude",
        lambda prompt, max_tokens=4096: '{"coverage_score": 90, "findings": [], "final_score": 95, "feedback_text": "great job"}',
    )
    result = _score()
    assert result["findings"] == []
    assert result["final_score"] == 95


def test_a_malformed_individual_finding_is_dropped_not_fatal(monkeypatch):
    """One finding missing its required "claim" field degrades to
    "dropped" rather than crashing the whole scoring call - schema
    validation happens per-finding (schemas.Round4Finding), same
    reasoning as poc_ai_judge's _safe_fallback."""
    monkeypatch.setattr(
        llm_service, "_call_claude",
        lambda prompt, max_tokens=4096: (
            '{"coverage_score": 50, "findings": ['
            '{"severity": "low", "evidence": []}, '
            '{"claim": "ok", "severity": "low", "evidence": []}'
            '], "final_score": 50, "feedback_text": ""}'
        ),
    )
    result = _score()
    assert len(result["findings"]) == 1
    assert result["findings"][0]["claim"] == "ok"
