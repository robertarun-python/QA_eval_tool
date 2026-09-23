"""
Round 3 idea provenance (first mention) - see round3_coding_scoring.txt's
"Idea provenance" paragraph. Run B found the scorer credited a candidate
with "proactively" moving to an O(n) approach the assistant had explained
one turn earlier: the old rule only compared each code_edit with that same
turn's instruction, never who said the idea first. These pin the offline
half - the rule is in the prompt, the scorer sees every turn (both sides,
in order), and an "assistant introduced" entry reaches HR via misses_json.
The model-judged half (Scenarios A/B/C and Run B) is in
tests/replay/test_replay_r3_provenance.py.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.models import Scenario, Submission, Round3Turn, RoundStatus, ScenarioStatus, ExperienceBand, User, Role
from app.services import scoring_service, llm_service, execution_service

CASES = json.loads((Path(__file__).parent / "replay" / "r3_provenance_cases.json").read_text())


def _scoring_prompt(monkeypatch):
    prompts = []

    def fake_claude(prompt, max_tokens=4096):
        prompts.append(prompt)
        return json.dumps({"correctness_score": 100, "precision_score": 90, "efficiency_score": 90,
                           "independent_judgment_score": 90, "final_score": 90, "misses": [],
                           "guardrail_violations": [], "feedback_text": "ok"})

    monkeypatch.setattr(llm_service, "_call_claude", fake_claude)
    llm_service.score_round3_coding(scenario_description="d", expected_approach="e", conversation_so_far=[], test_results=[])
    return prompts[0]


def test_scoring_prompt_has_the_first_mention_rule(monkeypatch):
    prompt = _scoring_prompt(monkeypatch)
    assert "belongs to whoever FIRST introduced it anywhere in the conversation" in prompt
    assert "check BOTH sides of every turn - each candidate_prompt AND each response_message" in prompt
    assert '"Turn N: assistant introduced <idea>; candidate adopted it in turn M"' in prompt
    assert "give no origin credit for it in efficiency_score or independent_judgment_score" in prompt
    assert "precision_score may still credit how completely and accurately the candidate directed its implementation" in prompt
    assert "independently, proactively or on their own" in prompt


def test_scoring_prompt_keeps_candidate_ownership_and_does_not_double_penalise(monkeypatch):
    prompt = _scoring_prompt(monkeypatch)
    assert "Ownership never moves the other way" in prompt
    assert "does NOT make it the assistant's, and the candidate keeps full credit for it" in prompt
    assert "is not by itself a guardrail violation and must never lower any score on its own" in prompt
    assert "never also listed or penalised here as (a)" in prompt


def test_scorer_receives_every_turn_in_order_with_both_sides(client, monkeypatch):
    """Scenario A's transcript, stored as real Round3Turn rows (including
    the explain turn where the assistant introduces the idea), reaches the
    scorer unchanged and in order - and an "assistant introduced" entry
    comes back out in misses_json for HR."""
    from app.database import SessionLocal

    case = next(c for c in CASES["cases"] if c["id"] == "A_ai_originated")
    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        scenario = Scenario(
            round_number=3, title="Second-Largest Distinct Value", description=CASES["scenario_description"],
            experience_band=ExperienceBand.junior, created_by=candidate.id, status=ScenarioStatus.published,
            is_live=True, time_limit_minutes=30,
            reference_json={"test_cases": [{"input": "3,1,4", "expected_output": "3", "description": "basic"}],
                            "expected_approach": "e"},
        )
        db.add(scenario)
        db.commit()
        submission = Submission(user_id=candidate.id, scenario_id=scenario.id, round_number=3, status=RoundStatus.submitted,
                                started_at=datetime.utcnow(), content={"language": "python", "draft_prompt": ""})
        db.add(submission)
        db.commit()
        for t in case["conversation_so_far"]:
            db.add(Round3Turn(submission_id=submission.id, language="python", **t))
        db.commit()
        db.refresh(submission)

        seen = {}
        violation = "Turn 3: assistant introduced tracking the largest and second-largest values; candidate adopted it in turn 4"

        def fake_score(**kwargs):
            seen.update(kwargs)
            return {"correctness_score": 100, "precision_score": 90, "efficiency_score": 40, "independent_judgment_score": 40,
                    "final_score": 70, "misses": [], "guardrail_violations": [violation], "feedback_text": "ok"}

        monkeypatch.setattr(llm_service, "score_round3_coding", fake_score)
        monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
            stdout="3\n", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1))
        score = scoring_service.score_round3_submission(db, submission)

        sent = seen["conversation_so_far"]
        assert [t["turn_number"] for t in sent] == [1, 2, 3, 4]
        assert [t["response_kind"] for t in sent] == ["code_edit", "code_edit", "explain", "code_edit"]
        for sent_turn, original in zip(sent, case["conversation_so_far"]):
            assert sent_turn["candidate_prompt"] == original["candidate_prompt"]
            assert sent_turn["response_message"] == original["response_message"]
        assert "scanning the list twice" in sent[2]["response_message"]  # the assistant's idea, visible to the scorer
        assert f"Guardrail: {violation}" in score.misses_json
    finally:
        db.close()


def test_run_b_fixture_keeps_the_clarify_and_explain_turns():
    """The real Run B transcript used by the replay case: both clarify
    turns and the explain turn (turn 6, where the assistant gave the O(n)
    idea) are present with their response_message, in order."""
    run_b = next(c for c in CASES["cases"] if c["id"] == "RunB_submission_193")
    turns = run_b["conversation_so_far"]
    assert [t["turn_number"] for t in turns] == list(range(1, 9))
    assert [t["response_kind"] for t in turns][:2] == ["clarify", "clarify"]
    assert turns[5]["response_kind"] == "explain"
    assert "O(n)" in turns[5]["response_message"]
    assert "biggest = max(nums)" in turns[6]["code_after"]
