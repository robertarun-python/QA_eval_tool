"""
Round 3 Priority 5 (D) - scoring attribution for a dropped requirement.
The scorer saw Run B's case-7 code count repeats after the candidate said
"distinct" and blamed the candidate ("you count total occurrences"). Now
scoring_service marks each assistant code_edit that dropped a stated
distinct/unique/no-duplicates requirement ("dropped_requirement", the same
pattern as "large_paste"), and round3_coding_scoring.txt tells the scorer
that mark is the assistant's mistake. Offline: the marks, the prompt rule,
and HR seeing the entry - the model's own judgement needs a live run.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


from app.models import Scenario, Submission, Round3Turn, RoundStatus, ScenarioStatus, ExperienceBand, User, Role
from app.services import scoring_service, llm_service, execution_service

READ = "nums = list(map(int, input().split(',')))"
DROPPED = READ + "\nfor num in nums:\n    count = 0\n    for other in nums:\n        if other > num:\n            count += 1"
KEPT = READ + "\nfor num in nums:\n    seen = []\n    for other in nums:\n        if other > num and other not in seen:\n            seen.append(other)"
WITH_SET = READ + "\nfor num in nums:\n    count = len({o for o in set(nums) if o > num})"
DISTINCT = "For each number, use a nested loop to count how many distinct numbers in nums are bigger than it."


def _t(n, prompt, kind="code_edit", code=None, message="Done."):
    return {"turn_number": n, "candidate_prompt": prompt, "response_kind": kind, "response_message": message, "code_after": code}


def _marks(conversation):
    payload = [dict(t) for t in conversation]
    scoring_service._flag_dropped_requirements(payload)
    return {t["turn_number"]: t.get("dropped_requirement") for t in payload}


# ---- 1-6, 8: which turns get marked ----

def test_1_assistant_dropping_distinct_is_marked():
    assert _marks([_t(1, "Read the line into nums.", code=READ), _t(2, DISTINCT, code=DROPPED)]) == {1: None, 2: "distinct numbers"}


def test_2_assistant_keeping_distinct_is_not_marked():
    assert _marks([_t(1, DISTINCT, code=KEPT)]) == {1: None}


def test_3_candidates_named_set_kept_is_not_marked():
    assert _marks([_t(1, DISTINCT.replace("use a nested loop", "use a set"), code=WITH_SET)]) == {1: None}


def test_4_asking_how_is_not_a_dropped_requirement():
    conv = [_t(1, DISTINCT, kind="clarify", message="How do you want repeated values to be left out?"),
            _t(2, "Keep a list called seen and skip values already in it.", code=KEPT)]
    assert _marks(conv) == {1: None, 2: None}


def test_4b_answer_to_the_question_is_checked_against_the_original_requirement():
    conv = [_t(1, DISTINCT, kind="clarify", message="How do you want repeated values to be left out?"),
            _t(2, "Use a nested loop.", code=DROPPED)]
    assert _marks(conv)[2] == "distinct numbers"


def test_5_later_fix_by_the_candidate_is_not_marked():
    conv = [_t(1, DISTINCT, code=DROPPED),
            _t(2, "It counts repeats. Keep a list called seen and only count values not in seen.", code=KEPT)]
    assert _marks(conv) == {1: "distinct numbers", 2: None}


def test_6_dropped_requirement_is_marked_separately_from_added_code():
    """Only the dropped requirement is marked here - unrequested additions stay
    the scorer's Assistant-made decisions check, never merged into this mark."""
    over = DROPPED + "\nprint(sorted(nums))"
    payload = [_t(1, DISTINCT, code=over)]
    scoring_service._flag_dropped_requirements(payload)
    assert payload[0]["dropped_requirement"] == "distinct numbers"
    assert set(payload[0]) == {"turn_number", "candidate_prompt", "response_kind", "response_message", "code_after", "dropped_requirement"}


def test_8_candidates_own_code_is_never_marked():
    assert _marks([_t(1, DISTINCT, kind="direct_edit", code=DROPPED)]) == {1: None}


def test_7_priority_2_provenance_transcripts_get_no_mark():
    cases = json.loads((Path(__file__).parent / "replay" / "r3_provenance_cases.json").read_text())["cases"]
    for case in cases:
        assert all(v is None for v in _marks(case["conversation_so_far"]).values()), case["id"]


# ---- the scoring prompt ----

def _prompt(monkeypatch):
    prompts = []
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: (prompts.append(p), json.dumps({
        "correctness_score": 0, "precision_score": 0, "efficiency_score": 0, "independent_judgment_score": 0,
        "final_score": 0, "misses": [], "guardrail_violations": [], "feedback_text": ""}))[1])
    llm_service.score_round3_coding(scenario_description="d", expected_approach="e", conversation_so_far=[], test_results=[])
    return prompts[0]


def test_scoring_prompt_blames_the_assistant_for_a_marked_turn(monkeypatch):
    p = _prompt(monkeypatch)
    assert 'a code_edit turn may carry "dropped_requirement"' in p
    assert "only distinct / unique / no duplicates are tracked" in p
    assert "That was the assistant's mistake, not the candidate's" in p
    assert '"Turn N: assistant dropped the candidate\'s requirement \\"<dropped_requirement>\\""' in p
    assert "never count it against the candidate in precision_score or independent_judgment_score" in p
    assert "a test failure caused only by that dropped requirement is not the candidate's correctness gap" in p
    assert "credit that diagnosis as usual" in p
    assert "the same mistake is never listed or penalised twice" in p
    assert "asks the candidate how to keep the requirement is a question, not a dropped requirement" in p
    assert "plus each dropped requirement per Dropped requirements above" in p


def test_priority_2_and_added_code_rules_are_unchanged(monkeypatch):
    p = _prompt(monkeypatch)
    assert "belongs to whoever FIRST introduced it anywhere in the conversation" in p
    assert '"Turn N: assistant introduced <idea>; candidate adopted it in turn M"' in p
    assert '"Turn N: assistant chose <what> without being asked"' in p


# ---- end to end: the mark reaches the scorer, the entry reaches HR ----

def test_marked_turn_reaches_the_scorer_and_hr(client, monkeypatch):
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()
        scenario = Scenario(round_number=3, title="t", description="d", experience_band=ExperienceBand.junior, created_by=candidate.id,
                            status=ScenarioStatus.published, is_live=True, time_limit_minutes=30,
                            reference_json={"test_cases": [{"input": "4,8", "expected_output": "4", "description": ""}], "expected_approach": "e"})
        db.add(scenario)
        db.commit()
        submission = Submission(user_id=candidate.id, scenario_id=scenario.id, round_number=3, status=RoundStatus.submitted,
                                started_at=datetime.utcnow(), content={"language": "python", "draft_prompt": ""})
        db.add(submission)
        db.commit()
        for t in [_t(1, "Read the line into nums.", code=READ), _t(2, DISTINCT, code=DROPPED)]:
            db.add(Round3Turn(submission_id=submission.id, language="python", **t))
        db.commit()
        db.refresh(submission)

        seen = {}
        entry = "Turn 2: assistant dropped the candidate's requirement \"distinct numbers\""

        def fake_score(**kwargs):
            seen.update(kwargs)
            return {"correctness_score": 50, "precision_score": 90, "efficiency_score": 50, "independent_judgment_score": 90,
                    "final_score": 70, "misses": [], "guardrail_violations": [entry], "feedback_text": "ok"}

        monkeypatch.setattr(llm_service, "score_round3_coding", fake_score)
        monkeypatch.setattr(execution_service, "run_code", lambda **kwargs: execution_service.ExecutionResult(
            stdout="4\n", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1))
        score = scoring_service.score_round3_submission(db, submission)

        assert seen["conversation_so_far"][1]["dropped_requirement"] == "distinct numbers"
        assert "dropped_requirement" not in seen["conversation_so_far"][0]
        assert f"Guardrail: {entry}" in score.misses_json
        assert score.raw_llm_response_json["conversation"][1]["dropped_requirement"] == "distinct numbers"
    finally:
        db.close()
