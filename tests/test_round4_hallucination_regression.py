"""
Permanent regression tests for the Round 4 hallucinated-evaluation bug
actually encountered: an evaluator produced findings like "Welcome label
was missing" and "zero follow-up questions" against a transcript that
supported neither. tests/fixtures/round4_hallucination_regression.py
pins down that exact transcript; these tests pin the deterministic
evidence auditor's behavior against it permanently - no live Claude
call, no dependence on Round 4's execution/UI behavior. See
test_round4.py::test_round4_full_pipeline_rejects_the_actual_hallucinated_findings
for the same fixture run through the full HTTP scoring pipeline.
"""
from app.services.round4_evidence_audit import audit_round4_findings, SEVERITY_WEIGHTS

from .fixtures.round4_hallucination_regression import (
    GENUINE_FOLLOWUP_FINDING,
    HALLUCINATED_WELCOME_LABEL_FINDING,
    HALLUCINATED_ZERO_FOLLOWUP_FINDING,
    TEST_CASES,
    TURN_1_BOOKING_200,
    TURN_2_BOOKING_409,
    TURN_3_INVALID_LOGIN_401,
)


def _audit(findings):
    return audit_round4_findings(TEST_CASES, findings)


def test_1_occupied_slot_test_case_is_recognized():
    finding = {
        "claim": "Booking a slot that is already occupied: verified the booking endpoint against slot 5512",
        "severity": "low",
        "evidence": [{"turn": TURN_1_BOOKING_200, "quote": "Booking request for slot 5512 returned HTTP 200"}],
    }
    report = _audit([finding])
    assert report.findings[0].status == "SUPPORTED"


def test_2_first_booking_result_http_200_is_preserved_as_observed():
    finding = {
        "claim": "First booking attempt for the already-occupied slot returned HTTP 200",
        "severity": "low",
        "evidence": [{"turn": TURN_1_BOOKING_200, "quote": "HTTP 200 with success=true and status=confirmed"}],
    }
    assert _audit([finding]).findings[0].status == "SUPPORTED"


def test_3_second_booking_result_http_409_is_preserved_as_observed():
    finding = {
        "claim": "Re-run of the booking attempt returned HTTP 409 SLOT_ALREADY_BOOKED",
        "severity": "low",
        "evidence": [{"turn": TURN_2_BOOKING_409, "quote": "HTTP 409 with SLOT_ALREADY_BOOKED"}],
    }
    assert _audit([finding]).findings[0].status == "SUPPORTED"


def test_4_turn_2_is_recognized_as_a_genuine_followup():
    # A claim that credits the follow-up must be accepted...
    assert _audit([GENUINE_FOLLOWUP_FINDING]).findings[0].status == "SUPPORTED"
    # ...and a claim that denies any follow-up ever happened must be struck down,
    # since turn 2 is a direct, explicit challenge of turn 1's result.
    assert _audit([HALLUCINATED_ZERO_FOLLOWUP_FINDING]).findings[0].status == "CONTRADICTED"


def test_5_invalid_credentials_test_case_is_recognized():
    finding = {
        "claim": "Invalid login credentials: verified the login endpoint rejects a wrong password",
        "severity": "low",
        "evidence": [{"turn": TURN_3_INVALID_LOGIN_401, "quote": "Invalid credentials returned HTTP 401"}],
    }
    assert _audit([finding]).findings[0].status == "SUPPORTED"


def test_6_http_401_is_recognized_as_the_observed_result():
    finding = {
        "claim": "Login with invalid credentials correctly returned HTTP 401 INVALID_CREDENTIALS",
        "severity": "low",
        "evidence": [{"turn": TURN_3_INVALID_LOGIN_401, "quote": "HTTP 401 INVALID_CREDENTIALS"}],
    }
    assert _audit([finding]).findings[0].status == "SUPPORTED"


def test_7_welcome_label_finding_cannot_become_a_scored_weakness():
    """The canonical hallucination this whole regression suite exists
    for: nothing in the transcript mentions "Welcome" at all, so this
    finding - even though it cites a turn and a quote - must not
    survive, and must not cost the candidate any points."""
    report = _audit([HALLUCINATED_WELCOME_LABEL_FINDING])
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.surviving_claims() == []
    assert report.score_adjustment() == SEVERITY_WEIGHTS["medium"]


def test_8_zero_followup_finding_cannot_become_a_valid_finding():
    report = _audit([HALLUCINATED_ZERO_FOLLOWUP_FINDING])
    assert report.findings[0].status == "CONTRADICTED"
    assert report.surviving_claims() == []
    assert report.score_adjustment() == SEVERITY_WEIGHTS["high"]


def test_9_does_not_confuse_the_409_result_with_the_200_result():
    # Citing turn 2 (the 409 re-run) but with the FIRST attempt's 200
    # text is a fabricated quote for that specific turn - must not validate.
    finding = {
        "claim": "Booking a slot that is already occupied: the API accepted the duplicate booking",
        "severity": "low",
        "evidence": [{"turn": TURN_2_BOOKING_409, "quote": "HTTP 200 with success=true and status=confirmed"}],
    }
    assert _audit([finding]).findings[0].status == "NOT_ESTABLISHED"


def test_10_does_not_conclude_the_first_attempt_passed_because_the_second_returned_409():
    # The same conflation in the other direction: citing turn 1 (the
    # original 200 attempt) with the SECOND attempt's 409 text. Turn 1's
    # own record never contains a 409, so this must not validate either -
    # the first attempt cannot be vindicated by borrowing the second
    # attempt's result.
    finding = {
        "claim": "Booking a slot that is already occupied: the first attempt correctly rejected the duplicate booking",
        "severity": "low",
        "evidence": [{"turn": TURN_1_BOOKING_200, "quote": "HTTP 409 with SLOT_ALREADY_BOOKED"}],
    }
    assert _audit([finding]).findings[0].status == "NOT_ESTABLISHED"


def test_11_no_evidence_from_another_round_or_candidate_can_affect_the_result():
    # A quote that never appears anywhere in THIS transcript - as if it
    # leaked in from a different candidate's session or a different
    # round - is rejected exactly like any other fabricated quote...
    foreign_quote_finding = {
        "claim": "Candidate also verified the round 1 doctor-search flow",
        "severity": "low",
        "evidence": [{"turn": TURN_1_BOOKING_200, "quote": "doctor search returned 12 matching results"}],
    }
    assert _audit([foreign_quote_finding]).findings[0].status == "NOT_ESTABLISHED"

    # ...even though the exact same finding IS legitimately supported
    # against the session it's actually about - proving the auditor only
    # ever consults the transcript it is explicitly given, never
    # something true elsewhere.
    unrelated_session = [{
        "title": "Some other candidate's test case",
        "turns": [{
            "turn_number": 1,
            "candidate_prompt": "doctor search",
            "model_response": {
                "response_text": "",
                "steps": [],
                "observed_result": "doctor search returned 12 matching results",
                "status": "pass",
            },
        }],
    }]
    assert audit_round4_findings(unrelated_session, [foreign_quote_finding]).findings[0].status == "SUPPORTED"
    assert _audit([foreign_quote_finding]).findings[0].status == "NOT_ESTABLISHED"
