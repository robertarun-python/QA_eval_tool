"""
Regression tests for stateful Round 4 test cases: one candidate action
(e.g. booking a slot) can change the state a LATER attempt against the
same resource observes. The deterministic evidence auditor
(services.round4_evidence_audit) must judge each attempt against its
OWN observed result and the candidate's OWN oracle for that attempt -
never let a later attempt's result overwrite, invalidate, or "resolve"
an earlier attempt's own finding, and never let an earlier attempt
retroactively poison a later one either. See prompts/round4_scoring.txt's
State awareness section (Original defect vs. Subsequent protection) for
the corresponding instruction given to the LLM scorer - these tests pin
the deterministic backstop that holds regardless of what the LLM does.
"""
from app.services.round4_evidence_audit import audit_round4_findings, SEVERITY_WEIGHTS


def _test_cases(turns_by_case):
    return [
        {
            "title": title,
            "turns": [
                {"turn_number": i + 1, "candidate_prompt": prompt, "model_response": response}
                for i, (prompt, response) in enumerate(turns)
            ],
        }
        for title, turns in turns_by_case.items()
    ]


def _response(observed_result, status="pass"):
    return {"response_text": "", "steps": [], "observed_result": observed_result, "status": status}


def test_1_first_attempt_200_then_409_the_original_defect_finding_remains_fail():
    """Attempt 1: slot booked, candidate books it anyway, API returns
    HTTP 200 (oracle required 4xx) -> FAIL. Attempt 2: same request
    repeated, API returns HTTP 409. Expected: the finding about attempt
    1 stays SUPPORTED (a real, standing defect) - the existence of
    attempt 2's clean result must not touch it."""
    test_cases = _test_cases({
        "Booking conflict": [
            ("book slot 5512, already booked, expect 4xx", _response("HTTP 200 with success=true and status=confirmed", status="fail")),
            ("it should not have returned 200 right?", _response("HTTP 409 SLOT_ALREADY_BOOKED", status="pass")),
        ],
    })
    original_defect = {
        "claim": "Booking conflict: attempt 1 returned HTTP 200 for an already-booked slot, violating the candidate's 4xx oracle",
        "severity": "high",
        "evidence": [{"turn": 1, "quote": "HTTP 200 with success=true and status=confirmed"}],
    }
    report = audit_round4_findings(test_cases, [original_defect])
    assert report.findings[0].status == "SUPPORTED"
    assert report.surviving_claims() == [original_defect["claim"]]


def test_2_first_attempt_409_passes_immediately():
    """No state conflict at all here: the very first attempt already
    returns HTTP 409 against an already-booked slot. Expected: PASS
    (SUPPORTED) against an oracle requiring 4xx, evidenced by that one
    attempt alone."""
    test_cases = _test_cases({
        "Booking conflict": [
            ("book slot 5512, already booked, expect 4xx", _response("HTTP 409 SLOT_ALREADY_BOOKED", status="pass")),
        ],
    })
    finding = {
        "claim": "Booking conflict: correctly rejected the already-booked slot with HTTP 409 on the first attempt",
        "severity": "low",
        "evidence": [{"turn": 1, "quote": "HTTP 409 SLOT_ALREADY_BOOKED"}],
    }
    report = audit_round4_findings(test_cases, [finding])
    assert report.findings[0].status == "SUPPORTED"


def test_3_200_followed_by_another_200_both_observations_stay_separate():
    """Two consecutive attempts both return HTTP 200 (the defect was
    never actually fixed). Expected: a finding about attempt 1 and a
    finding about attempt 2 are each independently evidenced and
    independently accepted - neither collapses into or depends on the
    other."""
    test_cases = _test_cases({
        "Booking conflict": [
            ("book slot 5512, already booked, expect 4xx", _response("HTTP 200 with success=true", status="fail")),
            ("try again, still expect 4xx", _response("HTTP 200 with success=true again", status="fail")),
        ],
    })
    finding_attempt_1 = {
        "claim": "Booking conflict: attempt 1 returned HTTP 200 instead of a 4xx error",
        "severity": "high",
        "evidence": [{"turn": 1, "quote": "HTTP 200 with success=true"}],
    }
    finding_attempt_2 = {
        "claim": "Booking conflict: attempt 2 also returned HTTP 200, confirming the defect is not a fluke",
        "severity": "high",
        "evidence": [{"turn": 2, "quote": "HTTP 200 with success=true again"}],
    }
    report = audit_round4_findings(test_cases, [finding_attempt_1, finding_attempt_2])
    assert report.findings[0].status == "SUPPORTED"
    assert report.findings[1].status == "SUPPORTED"
    assert set(report.surviving_claims()) == {finding_attempt_1["claim"], finding_attempt_2["claim"]}


def test_4_explicit_followup_investigation_is_not_classified_as_no_followup():
    """The candidate explicitly challenges the unexpected first result
    before re-testing - a genuine follow-up. Expected: a finding
    claiming zero follow-up must be struck down (CONTRADICTED), and a
    finding crediting the follow-up must be accepted."""
    test_cases = _test_cases({
        "Booking conflict": [
            ("book slot 5512, already booked, expect 4xx", _response("HTTP 200 with success=true", status="fail")),
            ("the API should have given error. it should not have returned 200 right?", _response("HTTP 409 SLOT_ALREADY_BOOKED", status="pass")),
        ],
    })
    no_followup_claim = {
        "claim": "Candidate accepted the HTTP 200 result with zero follow-up investigation",
        "severity": "high",
        "evidence": [{"turn": 1, "quote": "HTTP 200 with success=true"}],
    }
    genuine_followup_claim = {
        "claim": "Booking conflict: candidate questioned the unexpected HTTP 200 before re-testing",
        "severity": "low",
        "evidence": [{"turn": 2, "quote": "it should not have returned 200"}],
    }
    report = audit_round4_findings(test_cases, [no_followup_claim, genuine_followup_claim])
    assert report.findings[0].status == "CONTRADICTED"
    assert report.findings[1].status == "SUPPORTED"
    assert report.surviving_claims() == [genuine_followup_claim["claim"]]


def test_5_state_changing_attempts_are_ordered_chronologically_in_the_evidence_model():
    """The evidence model's turn numbering must reflect the actual order
    attempts happened in - the auditor can only reason about "before"
    and "after" a state change if turn 1 really did happen before turn
    2. Build a longer, three-attempt sequence and confirm each global
    turn index resolves to that exact attempt's own text, in order."""
    test_cases = _test_cases({
        "Booking conflict": [
            ("attempt 1", _response("attempt 1 result: HTTP 200")),
            ("attempt 2", _response("attempt 2 result: HTTP 200")),
            ("attempt 3", _response("attempt 3 result: HTTP 409")),
        ],
    })
    findings = [
        {"claim": "Attempt 1 returned HTTP 200", "severity": "low", "evidence": [{"turn": 1, "quote": "attempt 1 result: HTTP 200"}]},
        {"claim": "Attempt 2 returned HTTP 200", "severity": "low", "evidence": [{"turn": 2, "quote": "attempt 2 result: HTTP 200"}]},
        {"claim": "Attempt 3 returned HTTP 409", "severity": "low", "evidence": [{"turn": 3, "quote": "attempt 3 result: HTTP 409"}]},
        # Deliberately mismatched pairing (turn 1's citation used for
        # what is actually attempt 3's text) - must fail, proving the
        # model doesn't just accept any quote that exists SOMEWHERE in
        # the session regardless of chronological position.
        {"claim": "Attempt 1 returned HTTP 409", "severity": "low", "evidence": [{"turn": 1, "quote": "attempt 3 result: HTTP 409"}]},
    ]
    report = audit_round4_findings(test_cases, findings)
    assert [f.status for f in report.findings] == ["SUPPORTED", "SUPPORTED", "SUPPORTED", "NOT_ESTABLISHED"]
