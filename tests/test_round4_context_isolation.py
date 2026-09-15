"""
Cross-round / cross-candidate context-isolation regression tests for
Round 4 scoring. Complements test_round4_hallucination_regression.py
(which proves an ungrounded finding can't survive the post-LLM audit)
by proving the boundary one layer earlier: FORBIDDEN evidence (another
round's transcript, another candidate's submission, a previous scoring
result) never even reaches the prompt sent to the LLM in the first
place - see scoring_service._build_round4_evidence /
_build_round1_reference_context and llm_service.score_round4_conversation's
enforced key whitelist.

Uses the `client` fixture purely to get a real (in-memory) DB session,
same pattern as test_scoring_service_round3_coding.py - these tests
call scoring_service.score_round4_submission directly and capture the
actual prompt text llm_service would have sent to Claude (via
monkeypatching _call_claude), so no live LLM call is ever made and the
assertions are fully deterministic.
"""
from datetime import datetime

from app.models import (
    ConversationTurn, ExperienceBand, Role, Round3Turn, Round4TestCase,
    RoundStatus, Scenario, ScenarioStatus, Score, Submission, User,
)
from app.services import llm_service, scoring_service


def _make_scenario(db, candidate, round_number, title, description):
    scenario = Scenario(
        round_number=round_number, title=title, description=description,
        experience_band=ExperienceBand.junior, created_by=candidate.id,
        status=ScenarioStatus.published, is_live=True, time_limit_minutes=30,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return scenario


def _make_submission(db, candidate, scenario, round_number, **kwargs):
    submission = Submission(
        user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
        status=kwargs.pop("status", RoundStatus.submitted),
        started_at=kwargs.pop("started_at", datetime.utcnow()),
        **kwargs,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def _add_round4_test_case_with_turn(db, submission, title, candidate_prompt, observed_result):
    tc = Round4TestCase(submission_id=submission.id, title=title)
    db.add(tc)
    db.commit()
    db.refresh(tc)
    turn = ConversationTurn(
        submission_id=submission.id, test_case_id=tc.id, turn_number=1,
        candidate_prompt=candidate_prompt,
        model_response={"response_text": "", "steps": [], "observed_result": observed_result, "status": "pass"},
    )
    db.add(turn)
    db.commit()
    return tc


def _capture_prompt(monkeypatch):
    """Monkeypatches llm_service._call_claude to record the exact prompt
    text it was given and return a fixed, valid, empty-findings response -
    no network call, no real model, fully deterministic."""
    captured = {}

    def fake_call_claude(prompt, max_tokens=4096):
        captured["prompt"] = prompt
        return '{"coverage_score": 50, "findings": [], "final_score": 50, "feedback_text": "ok"}'

    monkeypatch.setattr(llm_service, "_call_claude", fake_call_claude)
    return captured


def test_round1_finding_is_reference_only_and_never_appears_as_primary_evidence(client, monkeypatch):
    """Round 1 contains 'Candidate failed because Welcome label was
    missing.' The Round 4 transcript contains no such discussion.
    Expected: that text may legitimately appear under REFERENCE ONLY -
    ROUND 1 (it's the candidate's own round 1 work, used only to explain
    the scenario) but must never appear inside the PRIMARY EVIDENCE -
    CURRENT ROUND 4 section, which is the only section a finding may be
    drawn from."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()

        r1_scenario = _make_scenario(db, candidate, 1, "Doctor Appointment System", "Book, view, cancel appointments.")
        _make_submission(
            db, candidate, r1_scenario, 1,
            status=RoundStatus.scored, submitted_at=datetime.utcnow(),
            content=[{"description": "Login test", "expected_result": "Candidate failed because Welcome label was missing."}],
        )

        r4_scenario = _make_scenario(db, candidate, 4, "Automate the appointment flow", "Automate booking.")
        r4_submission = _make_submission(db, candidate, r4_scenario, 4)
        _add_round4_test_case_with_turn(
            db, r4_submission, "Invalid login", "try invalid creds",
            "Invalid credentials returned HTTP 401.",
        )

        captured = _capture_prompt(monkeypatch)
        scoring_service.score_round4_submission(db, r4_submission)
        prompt = captured["prompt"]

        primary_block = prompt[prompt.index("PRIMARY EVIDENCE - CURRENT ROUND 4"):prompt.index("END PRIMARY EVIDENCE - CURRENT ROUND 4")]
        reference_block = prompt[prompt.index("REFERENCE ONLY - ROUND 1"):prompt.index("END REFERENCE ONLY - ROUND 1")]

        assert "Welcome label was missing" in reference_block
        assert "Welcome label was missing" not in primary_block
        assert "HTTP 401" in primary_block
    finally:
        db.close()


def test_round3_action_never_reaches_the_round4_prompt(client, monkeypatch):
    """Round 3 contains a completely different candidate action. The
    Round 4 transcript does not contain it. Expected: the Round 4 prompt
    must not mention it at all - there is no field for Round 3 data in
    either evidence bucket (see llm_service.score_round4_conversation),
    so it can't be attributed to the Round 4 candidate."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()

        r1_scenario = _make_scenario(db, candidate, 1, "Doctor Appointment System", "Book, view, cancel appointments.")
        _make_submission(db, candidate, r1_scenario, 1, status=RoundStatus.scored, submitted_at=datetime.utcnow(), content=[])

        r3_scenario = _make_scenario(db, candidate, 3, "Second largest", "Find the second largest distinct value.")
        r3_submission = _make_submission(
            db, candidate, r3_scenario, 3,
            content={"language": "python", "draft_prompt": ""},
        )
        db.add(Round3Turn(
            submission_id=r3_submission.id, turn_number=1,
            candidate_prompt="use a recursive binary search to locate the pivot element",
            language="python", response_kind="explanation",
            response_message="Used recursion to reverse the input string as a warm-up.",
        ))
        db.commit()

        r4_scenario = _make_scenario(db, candidate, 4, "Automate the appointment flow", "Automate booking.")
        r4_submission = _make_submission(db, candidate, r4_scenario, 4)
        _add_round4_test_case_with_turn(
            db, r4_submission, "Invalid login", "try invalid creds",
            "Invalid credentials returned HTTP 401.",
        )

        captured = _capture_prompt(monkeypatch)
        scoring_service.score_round4_submission(db, r4_submission)
        prompt = captured["prompt"]

        assert "recursive binary search" not in prompt
        assert "reverse the input string" not in prompt
    finally:
        db.close()


def test_previous_round4_evaluation_has_zero_influence_on_a_retake(client, monkeypatch):
    """A previous evaluation result contains 'Candidate performed no
    follow-up.' The current Round 4 transcript contains an explicit
    follow-up. Expected: the previous evaluation must have zero
    influence - it must not appear in the new scoring prompt at all,
    since Round 4 scoring never reads another submission's Score."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        candidate = db.query(User).filter(User.role == Role.candidate).first()

        r1_scenario = _make_scenario(db, candidate, 1, "Doctor Appointment System", "Book, view, cancel appointments.")
        _make_submission(db, candidate, r1_scenario, 1, status=RoundStatus.scored, submitted_at=datetime.utcnow(), content=[])

        r4_scenario = _make_scenario(db, candidate, 4, "Automate the appointment flow", "Automate booking.")

        # An OLD, archived round 4 attempt with its own cached Score -
        # exactly the "previous evaluation result" / "cached evaluator
        # findings from another round" forbidden-evidence case.
        old_submission = _make_submission(
            db, candidate, r4_scenario, 4,
            status=RoundStatus.scored, submitted_at=datetime.utcnow(), archived=True,
        )
        db.add(Score(
            submission_id=old_submission.id, coverage_score=20, final_score=20,
            misses_json=["Candidate performed no follow-up."],
        ))
        db.commit()

        # The candidate's CURRENT, live round 4 attempt - this time with
        # an explicit follow-up.
        new_submission = _make_submission(db, candidate, r4_scenario, 4)
        tc = _add_round4_test_case_with_turn(
            db, new_submission, "Booking conflict", "book slot 5512",
            "Booking returned HTTP 200 even though the slot was already booked.",
        )
        db.add(ConversationTurn(
            submission_id=new_submission.id, test_case_id=tc.id, turn_number=2,
            candidate_prompt="the API should have given error. it should not have returned 200 right?",
            model_response={"response_text": "", "steps": [], "observed_result": "HTTP 409 SLOT_ALREADY_BOOKED", "status": "pass"},
        ))
        db.commit()

        captured = _capture_prompt(monkeypatch)
        scoring_service.score_round4_submission(db, new_submission)
        prompt = captured["prompt"]

        assert "Candidate performed no follow-up" not in prompt
        assert "the API should have given error" in prompt
    finally:
        db.close()
