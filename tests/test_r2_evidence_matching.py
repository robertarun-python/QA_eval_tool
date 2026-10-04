"""R2 evidence matching across serializations: the scorer sees each evidence block as json.dumps
text and may quote it with JSON escapes still in, or mixed with real characters. Such a quote is
the same evidence and must be found; anything not actually in the evidence must still not be.

Reproduces the production smoke of 2026-10-05 (submission 218): a genuine quote of test case 0's
run output, copied with an escaped \\" next to a real line break, was rejected and refunded 9
points. Synthetic text, no AI call."""
import json

from app.services import scoring_service

LABEL0 = "Test case 0: View upcoming EMI details"
LABEL1 = "Test case 1: Pay EMI"
CODE0 = 'driver.find_element(By.CSS_SELECTOR, \'[id="detail-emi-amount"]\')\nprint("checked")\n'
STDOUT0 = ('FAIL: step 4 - Check the page shows amount ₹5,250.00: Message: no such element: Unable to locate element: '
           '{"method":"css selector","selector":"[id="detail-emi-amount"]"}\n  (Session info: chrome=154.0.8037.0)\n')
# Exactly the production shape: \" escaped as in the JSON the scorer saw, the line break real.
MIXED_QUOTE = ('FAIL: step 4 - Check the page shows amount ₹5,250.00: Message: no such element: Unable to locate element: '
               '{\\"method\\":\\"css selector\\",\\"selector\\":\\"[id=\\"detail-emi-amount\\"]\\"}\n  (Session info: chrome=154.')
SCORES = {"automation_design": 14, "test_data_and_assertions": 8, "ai_usage": 16, "ai_output_review": 6, "execution_and_validation": 4}


def _block(label, index, code, stdout, stderr=""):
    return {"tc_index": index, "label": label, "design": {"title": label.split(": ", 1)[1], "steps": "1. Open the loan"},
            "final_code": code, "turns": [{"turn_number": 1, "candidate_prompt": 'Use the id "detail-emi-amount"',
                                           "response_kind": "code_edit", "response_message": "Encoded it."}],
            "code_edits": [], "execution_result": {"stdout": stdout, "stderr": stderr, "exit_code": 1},
            "synchronisation": {"verdict": "waits"}, "untraceable_literals": []}


def _blocks(stdout0=STDOUT0):
    return [_block(LABEL0, 0, CODE0, stdout0), _block(LABEL1, 1, "print('pay')\n", "PASS: paid\n")]


def _audit(findings, blocks=None, known_defects=None):
    blocks = blocks or _blocks()
    result = {"scores": dict(SCORES), "final_score": 48, "findings": findings, "feedback_text": "x"}
    scoring_service._r2_final_from_subscores(result)
    payload = [{"title": b["label"], "turns": [{"turn_number": t["turn_number"], "candidate_prompt": t["candidate_prompt"],
                                                "model_response": t.get("response_message", "")} for t in b["turns"]]} for b in blocks]
    supporting, audit_inputs = scoring_service._r2_audit_texts(blocks)
    misses, final, audit = scoring_service._round2_automation_findings_to_misses(
        result, payload, supporting, known_defects=known_defects or [], **audit_inputs)
    return misses, final, audit


def _finding(quote, label=LABEL0, severity="high", **extra):
    return {"claim": f"{label.split(':')[0]}: weakness", "severity": severity, "evidence": [{"quote": quote, "test_case": label}], **extra}


SUPPORTED_OTHER = _finding("PASS: paid", label=LABEL1, severity="medium")


def _status(audit, i=0):
    return audit["findings"][i]["evidence_status"]


# ---- the production failure ----

def test_production_mixed_escaped_quote_is_established_and_not_refunded():
    misses, final, audit = _audit([_finding(MIXED_QUOTE), SUPPORTED_OTHER])
    assert _status(audit) == "SUPPORTED"
    assert len(misses) == 2
    assert final == 48  # the sum of the areas - no refund for a genuine finding


# ---- A-D: equivalent representations of the same evidence ----

def test_a_escaped_double_quotes_match_the_raw_evidence():
    quote = '{\\"method\\":\\"css selector\\",\\"selector\\":\\"[id=\\"detail-emi-amount\\"]\\"}'
    assert quote.replace('\\"', '"') in STDOUT0
    _, _, audit = _audit([_finding(quote)])
    assert _status(audit) == "SUPPORTED"


def test_b_escaped_newline_matches_a_real_line_break():
    quote = '\\"detail-emi-amount\\"]\\"}\\n  (Session info: chrome=154'
    _, _, audit = _audit([_finding(quote)])
    assert _status(audit) == "SUPPORTED"
    _, _, audit = _audit([_finding('print(\\"checked\\")\\n')])  # candidate code, quoted escaped
    assert _status(audit) == "SUPPORTED"


def test_c_crlf_matches_lf():
    _, _, audit = _audit([_finding('"detail-emi-amount"]"}\n  (Session info')], blocks=_blocks(STDOUT0.replace("\n", "\r\n")))
    assert _status(audit) == "SUPPORTED"
    _, _, audit = _audit([_finding('"detail-emi-amount"]"}\r\n  (Session info')])
    assert _status(audit) == "SUPPORTED"


def test_d_surrounding_and_repeated_whitespace_matches():
    _, _, audit = _audit([_finding('   (Session   info:\tchrome=154.0.8037.0)   \n')])
    assert _status(audit) == "SUPPORTED"


def test_turn_quote_with_escaped_quotes_matches_the_turn():
    findings = [{"claim": "Test case 0: x", "severity": "low", "evidence": [{"turn": 1, "quote": 'Use the id \\"detail-emi-amount\\"'}]}]
    _, _, audit = _audit(findings)
    assert _status(audit) == "SUPPORTED"


# ---- E-G: integrity unchanged ----

def test_e_genuinely_different_quote_is_not_established():
    different = MIXED_QUOTE.replace("detail-emi-amount", "detail-emi-total")
    misses, final, audit = _audit([_finding(different), SUPPORTED_OTHER])
    assert _status(audit) == "NOT_ESTABLISHED"
    assert len(misses) == 1 and final > 48  # rejected -> bounded refund, as before


def test_f_fabricated_quote_is_not_established_in_any_form():
    for quote in ('FAIL: step 2 - login button not found', 'FAIL: step 2 - \\"login\\" button not found\\n',
                  'Unable to locate element: \\"login-btn\\"'):
        _, _, audit = _audit([_finding(quote)])
        assert _status(audit) == "NOT_ESTABLISHED", quote


def test_escaped_quote_tagged_with_the_wrong_test_case_is_still_rejected():
    _, _, audit = _audit([_finding(MIXED_QUOTE, label=LABEL1)])
    assert _status(audit) == "NOT_ESTABLISHED"
    assert audit["findings"][0]["evidence"][0]["test_case"] == LABEL1


def test_backslash_sequences_are_not_invented():
    # An escaped backslash then n is a backslash and an n, never a line break.
    blocks = [_block(LABEL0, 0, 'path = "C:\\\\new"\n', "ok\n")]
    _, _, audit = _audit([_finding('path = \\"C:\\\\\\\\new\\"')], blocks=blocks)
    assert _status(audit) == "SUPPORTED"
    # Undoing escapes twice would turn that backslash-backslash-n into a line break and match this:
    _, _, audit = _audit([_finding('path = "C:\new"')], blocks=blocks)  # "C:", a line break, "ew"
    assert _status(audit) == "NOT_ESTABLISHED"


def test_quote_characters_and_backslashes_still_count():
    for quote in ('selector:[id=detail-emi-amount]', "{'method':'css selector'}", 'path = C:\\new'):
        _, _, audit = _audit([_finding(quote)], blocks=[_block(LABEL0, 0, 'path = "C:\\new"\n', STDOUT0)])
        assert _status(audit) == "NOT_ESTABLISHED", quote


def test_case_and_word_breaks_still_count():
    for quote in ("fail: STEP 4 - check the page", "Unable to locateelement", "no such elem ent"):
        _, _, audit = _audit([_finding(quote)])
        assert _status(audit) == "NOT_ESTABLISHED", quote


D9 = {"id": "D9", "creditable": True, "requirement": "r", "actual_behaviour": "a", "observable_signal": "s",
      "observable_signals": ["no such element"], "targets": ["emi"]}


def test_g_candidate_code_quote_never_counts_as_run_output():
    code_quote = 'driver.find_element(By.CSS_SELECTOR, \'[id=\\"detail-emi-amount\\"]\')'
    _, _, audit = _audit([_finding(code_quote, kind="defect_detected", defect_id="D9")], known_defects=[D9])
    assert audit["findings"][0]["evidence_status"] == "UNVERIFIED_DETECTION"
    assert audit["findings"][0]["reason"] in ("not_shown_in_run_output", "evidence_not_established")


# ---- H: known-defect rules unchanged ----

def test_h_detection_citing_raw_run_output_is_verified_as_before():
    raw_quote = 'Message: no such element: Unable to locate element'
    _, final, audit = _audit([_finding(raw_quote, kind="defect_detected", defect_id="D9")], known_defects=[D9])
    assert audit["findings"][0]["evidence_status"] == "VERIFIED_DEFECT"
    assert final == 48


def test_h_detection_rules_still_compare_run_output_exactly():
    """The known-defect check (_verify_detection) and the no-double-penalty cancel rule keep
    comparing against the run output as recorded - this fix only widens whether a quote exists
    in the evidence, not what counts as a verified detection."""
    _, _, audit = _audit([_finding(MIXED_QUOTE, kind="defect_detected", defect_id="D9")], known_defects=[D9])
    assert audit["findings"][0]["evidence_status"] == "UNVERIFIED_DETECTION"
    assert audit["findings"][0]["reason"] == "not_shown_in_run_output"
    assert audit["needs_review"] is True


def test_json_package_evidence_still_found():
    blocks = _blocks()
    quote = json.dumps(blocks[0], indent=2).split("\n")[2].strip()  # a line of the package exactly as the scorer saw it
    _, _, audit = _audit([_finding(quote)], blocks=blocks)
    assert _status(audit) == "SUPPORTED"
