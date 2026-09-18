"""
Deterministic (no LLM) checks for services.round4_evidence_audit - the
post-scoring backstop that verifies every Round 4 finding's cited
evidence against the actual transcript before it's allowed to cost the
candidate any points. See that module's docstring and
scoring_service.score_round4_submission for how this plugs in.
"""
from app.services.round4_evidence_audit import audit_round4_findings, SEVERITY_WEIGHTS


def _test_cases(turns_by_case):
    """Build the same {title, turns: [...]} payload shape scoring_service
    passes to the scorer/auditor. turns_by_case: {title: [(candidate_prompt, model_response_dict), ...]}."""
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


def _response(observed_result="", response_text="", steps=None):
    return {"response_text": response_text, "steps": steps or [], "observed_result": observed_result, "status": "pass"}


def test_supported_finding_with_valid_quote():
    test_cases = _test_cases({
        "Invalid login": [("try invalid creds", _response(observed_result="HTTP 401 returned"))],
    })
    findings = [{
        "claim": "Correctly asserted HTTP 401 for invalid credentials",
        "severity": "low",
        "evidence": [{"turn": 1, "quote": "HTTP 401 returned"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "SUPPORTED"
    assert report.surviving_claims() == ["Correctly asserted HTTP 401 for invalid credentials"]
    assert report.score_adjustment() == 0


def test_unsupported_finding_with_no_evidence():
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login succeeded"))],
    })
    findings = [{"claim": "Never tested anything meaningful", "severity": "medium", "evidence": []}]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.surviving_claims() == []
    assert report.score_adjustment() == SEVERITY_WEIGHTS["medium"]


def test_contradicted_finding_zero_turns_claim_but_turns_exist():
    test_cases = _test_cases({
        "Booking conflict": [("book slot 5512", _response(observed_result="HTTP 200"))],
    })
    findings = [{
        "claim": "Booking conflict: created this test case but never executed it",
        "severity": "medium",
        "evidence": [{"test_case": "Booking conflict", "no_turns": True}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "CONTRADICTED"
    summary = report.summary()
    assert summary["contradicted"] == 1
    assert summary["supported"] == 0


def test_contradicted_finding_absolute_negative_claim_refuted_by_a_followup_turn():
    test_cases = _test_cases({
        "Booking conflict": [
            ("book slot 5512", _response(observed_result="HTTP 200, success: true")),
            ("the API should have given error. it should not have returned 200 right?",
             _response(observed_result="HTTP 409 SLOT_ALREADY_BOOKED")),
        ],
    })
    findings = [{
        "claim": "Accepted the result with zero follow-up investigation",
        "severity": "high",
        "evidence": [{"turn": 1, "quote": "HTTP 200, success: true"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "CONTRADICTED"
    assert report.score_adjustment() == SEVERITY_WEIGHTS["high"]


def test_invalid_turn_reference_out_of_range():
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login succeeded"))],
    })
    findings = [{
        "claim": "Something happened on a later turn",
        "severity": "low",
        "evidence": [{"turn": 7, "quote": "anything"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.summary()["invalid_references"] == 1


def test_fabricated_quote_not_present_in_referenced_turn():
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login succeeded, dashboard loaded"))],
    })
    findings = [{
        "claim": "Patient name was displayed on the dashboard",
        "severity": "low",
        "evidence": [{"turn": 1, "quote": "patient name displayed prominently"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "NOT_ESTABLISHED"
    # A fabricated quote inside a valid turn range is not the same failure
    # as an out-of-range turn - only the latter counts as invalid_references.
    assert report.summary()["invalid_references"] == 0


def test_whitespace_normalized_quote_still_matches():
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login   succeeded\nand the   dashboard loaded"))],
    })
    findings = [{
        "claim": "Confirmed successful login",
        "severity": "low",
        "evidence": [{"turn": 1, "quote": "Login succeeded and the dashboard loaded"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "SUPPORTED"


def test_absence_of_evidence_is_not_evidence_of_absence():
    """The canonical motivating example: transcript never mentions
    'Welcome' at all - the evaluator's claim that it was missing must not
    survive just because it sounds plausible."""
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login successful. Dashboard displayed."))],
    })
    findings = [{
        "claim": "Welcome label was missing after login",
        "severity": "medium",
        "evidence": [{"turn": 1, "quote": "Welcome label was missing"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.surviving_claims() == []


def test_explicit_evidence_of_absence_is_accepted():
    """Contrast with the above: when the transcript itself says the
    element was not found, that IS real evidence and should be accepted."""
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Logged in, but the word 'welcome' was not found on the page"))],
    })
    findings = [{
        "claim": "The expected 'welcome' text was not found after login",
        "severity": "medium",
        "evidence": [{"turn": 1, "quote": "the word 'welcome' was not found on the page"}],
    }]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "SUPPORTED"
    assert report.surviving_claims() == ["The expected 'welcome' text was not found after login"]


def test_multiple_findings_where_one_is_unsupported():
    """A mixed batch - one well-evidenced finding must survive
    independently of a second, unsupported one being dropped; neither
    finding's outcome should affect the other's."""
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login succeeded, dashboard loaded"))],
        "Search": [("search for widget", _response(observed_result="0 results returned"))],
    })
    findings = [
        {
            "claim": "Search: never verified the zero-results message text",
            "severity": "low",
            "evidence": [{"turn": 2, "quote": "0 results returned"}],
        },
        {
            "claim": "Login: patient name was displayed prominently on the dashboard",
            "severity": "medium",
            "evidence": [{"turn": 1, "quote": "patient name was displayed prominently"}],
        },
    ]
    report = audit_round4_findings(test_cases, findings)
    assert report.findings[0].status == "SUPPORTED"
    assert report.findings[1].status == "NOT_ESTABLISHED"
    assert report.surviving_claims() == ["Search: never verified the zero-results message text"]
    assert report.score_adjustment() == SEVERITY_WEIGHTS["medium"]
    summary = report.summary()
    assert summary == {"total_findings": 2, "supported": 1, "not_established": 1, "contradicted": 0, "invalid_references": 0}


def test_findings_detail_exposes_the_full_explainable_chain():
    """Finding -> Evidence -> Evidence status -> Score impact must be
    reconstructable from one persisted structure - see
    scoring_service.score_round4_submission's raw_llm_response_json["evidence_audit"]."""
    test_cases = _test_cases({
        "Login": [("go", _response(observed_result="Login succeeded"))],
    })
    findings = [
        {"claim": "Confirmed login succeeded", "severity": "low", "evidence": [{"turn": 1, "quote": "Login succeeded"}]},
        {"claim": "Never checked anything", "severity": "high", "evidence": []},
    ]
    report = audit_round4_findings(test_cases, findings)
    detail = report.findings_detail()
    assert detail == [
        {
            "finding": "Confirmed login succeeded",
            "severity": "low",
            "evidence_status": "SUPPORTED",
            "evidence": [{"turn": 1, "quote": "Login succeeded"}],
            "score_impact": 0,
        },
        {
            "finding": "Never checked anything",
            "severity": "high",
            "evidence_status": "NOT_ESTABLISHED",
            "evidence": [],
            "score_impact": SEVERITY_WEIGHTS["high"],
        },
    ]


# ---- supporting_texts: evidence that isn't a conversation turn ----
# Regression for a fidelity defect found in the real Round 2 flow. That
# round's rubric grades the final code, the execution result and the
# candidate's own written interpretation - all persisted - but only the AI
# turns were handed to the auditor. A legitimate execution_and_validation
# finding therefore cited nothing locatable, was marked NOT_ESTABLISHED,
# and its deduction was refunded: 98 became 100.

_TURNS = [{
    "title": "Automation session",
    "turns": [{"turn_number": 1, "candidate_prompt": "encode my step 1", "model_response": "Encoded it."}],
}]
_VALIDATION = (
    "Both tests passed (exit 0). What this does NOT prove is repeatability across "
    "separate runs, since each execution starts from a fresh in-process store."
)
_RUN_STDOUT = "test_reject_zero_amount passed\ntest_accept_valid_amount passed\n"
_FINAL_CODE = 'def test_reject_zero_amount():\n    assert response["id"] is None\n'


def test_the_98_to_100_case_without_supporting_texts_is_refunded():
    """The defect exactly as observed: a real finding quoting the
    candidate's own interpretation is unestablished, and its points come
    back. Pinned so a regression is unmistakable."""
    finding = {
        "claim": "The candidate's interpretation overstates what the run proves.",
        "severity": "low",
        "evidence": [{"quote": "each execution starts from a fresh in-process store"}],
    }
    report = audit_round4_findings(_TURNS, [finding])
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.score_adjustment() == SEVERITY_WEIGHTS["low"]  # 98 + 3 -> capped 100


def test_the_same_finding_is_supported_once_the_persisted_evidence_is_supplied():
    """The fix: the quote is real text the submission actually recorded,
    so the deduction stands and the intended score is preserved."""
    finding = {
        "claim": "The candidate's interpretation overstates what the run proves.",
        "severity": "low",
        "evidence": [{"quote": "each execution starts from a fresh in-process store"}],
    }
    report = audit_round4_findings(_TURNS, [finding], supporting_texts=[_VALIDATION, _RUN_STDOUT, _FINAL_CODE])
    assert report.findings[0].status == "SUPPORTED"
    assert report.score_adjustment() == 0          # nothing refunded
    assert report.surviving_claims() == [finding["claim"]]


def test_a_fabricated_quote_stays_unestablished_even_with_supporting_texts():
    """The whole point of the auditor must survive the fix: supplying more
    real evidence must not make an invented quote pass."""
    finding = {
        "claim": "The candidate said the database was corrupted.",
        "severity": "high",
        "evidence": [{"quote": "the database was corrupted beyond repair"}],
    }
    report = audit_round4_findings(_TURNS, [finding], supporting_texts=[_VALIDATION, _RUN_STDOUT, _FINAL_CODE])
    assert report.findings[0].status == "NOT_ESTABLISHED"
    assert report.score_adjustment() == SEVERITY_WEIGHTS["high"]
    assert report.surviving_claims() == []


def test_findings_about_execution_output_and_final_code_are_establishable():
    report = audit_round4_findings(
        _TURNS,
        [
            {"claim": "Run output shows only a pass line, no persistence check.",
             "severity": "medium", "evidence": [{"quote": "test_accept_valid_amount passed"}]},
            {"claim": "The code asserts only on the API's own response id.",
             "severity": "medium", "evidence": [{"quote": 'assert response["id"] is None'}]},
        ],
        supporting_texts=[_VALIDATION, _RUN_STDOUT, _FINAL_CODE],
    )
    assert [f.status for f in report.findings] == ["SUPPORTED", "SUPPORTED"]
    assert report.score_adjustment() == 0


def test_turn_quotes_still_work_and_supporting_texts_are_optional():
    """No behaviour change for the callers that pass nothing - the legacy
    round 4 and pilot flows must be byte-identical."""
    finding = {"claim": "Asked the assistant to encode step 1.", "severity": "low",
               "evidence": [{"turn": 1, "quote": "encode my step 1"}]}
    without = audit_round4_findings(_TURNS, [finding])
    with_support = audit_round4_findings(_TURNS, [finding], supporting_texts=[_VALIDATION])
    assert without.findings[0].status == "SUPPORTED"
    assert with_support.findings[0].status == "SUPPORTED"
    assert without.summary() == with_support.summary()


def test_empty_or_none_supporting_texts_change_nothing():
    finding = {"claim": "x", "severity": "low", "evidence": [{"quote": "nowhere to be found"}]}
    for support in (None, [], ["", "   "]):
        report = audit_round4_findings(_TURNS, [finding], supporting_texts=support)
        assert report.findings[0].status == "NOT_ESTABLISHED"
