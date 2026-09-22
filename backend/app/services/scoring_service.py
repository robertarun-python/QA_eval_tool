"""
Turns raw LLM output into a persisted Score row. Split out from
llm_service so llm_service stays "talk to Claude" only, and this file
is "what do we do with the answer" - easier to unit test scoring logic
without mocking the Anthropic client every time.
"""
import json
from datetime import datetime, timedelta

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from ..models import Submission, Score, RoundStatus, Scenario, User, AppSettings, CandidateSummary
from . import llm_service, execution_service, round4_evidence_audit

# Keep in sync with routers/candidate.py's ROUND_SEQUENCE - duplicated
# rather than imported to avoid a routers -> services -> routers import
# cycle (candidate.py already imports this module).
_ASSESSMENT_ROUND_SEQUENCE = (1, 2, 3, 4)


def _apply_provenance(score: Score, result: dict) -> None:
    """Pops _provenance off the raw LLM result (see llm_service.py's
    _scoring_provenance) and writes it onto the Score row, so it never
    ends up duplicated inside raw_llm_response_json too. Missing/empty
    when the caller is a test that monkeypatches llm_service's scoring
    function directly (bypassing the real prompt-loading/API call this
    comes from) - that's fine, these columns are all nullable."""
    provenance = result.pop("_provenance", None) or {}
    score.scoring_model = provenance.get("model")
    score.scoring_prompt_file = provenance.get("prompt_file")
    score.scoring_prompt_hash = provenance.get("prompt_hash")
    score.scored_at = datetime.utcnow()


def _get_or_create_score(db: Session, submission: Submission) -> Score:
    """A retry (see routers/hr.py's retry-scoring) re-runs one of the
    scorer functions below against a submission that may already have a
    Score row (e.g. HR hand-scored it after an earlier failure, then
    retried anyway) - reuse that row instead of inserting a second one
    and violating scores.submission_id's uniqueness constraint. A fresh
    real LLM score also isn't an HR override anymore, so clear any
    override audit fields the reused row was carrying."""
    score = submission.score
    if score is None:
        score = Score(submission_id=submission.id)
        db.add(score)
    else:
        score.original_final_score = None
        score.overridden_by_user_id = None
        score.override_note = None
        score.overridden_at = None
    return score


def score_round1_submission(db: Session, submission: Submission) -> Score:
    """Round 1: candidate's structured test-case rows vs. the scenario's
    HR-approved reference rows - same shape on both sides."""
    scenario = submission.scenario
    experience_band = scenario.experience_band.value if scenario.experience_band else "both"

    # The reference was already generated and HR-approved when the
    # scenario was published (see hr.py) - reused here rather than
    # generated fresh, so every candidate is scored against the exact
    # reference HR reviewed, not a new LLM roll each time.
    reference_rows = scenario.reference_json or []
    candidate_submission = json.dumps(submission.content or [], indent=2)

    result = llm_service.score_round1_submission(
        scenario_description=scenario.description,
        experience_band=experience_band,
        reference_cases=reference_rows,
        candidate_submission=candidate_submission,
    )

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = result.get("misses", [])
    score.concept_coverage_json = result.get("concept_coverage", [])
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"reference_rows": reference_rows, "scoring": result}
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


def score_round2_investigation(db: Session, submission: Submission) -> Score:
    """Round 2: candidate's submission is investigation-shaped (a list of
    areas checked + one root-cause conclusion, see schemas.Round2SubmissionCreate),
    scored against the scenario's still test-case-shaped reference (HR's
    review UI is unaffected by this - see llm_service.py's round 2 section)."""
    scenario = submission.scenario
    experience_band = scenario.experience_band.value if scenario.experience_band else "both"
    reference_rows = scenario.reference_json or []

    content = submission.content or {}
    candidate_investigation = content.get("investigation", [])
    candidate_root_cause = content.get("root_cause", "")

    result = llm_service.score_round2_submission(
        scenario_description=scenario.description,
        experience_band=experience_band,
        reference_steps=reference_rows,
        candidate_investigation=candidate_investigation,
        candidate_root_cause=candidate_root_cause,
    )

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = result.get("misses", [])
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"reference_rows": reference_rows, "scoring": result}
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


def score_round3_submission(db: Session, submission: Submission) -> Score:
    """Round 3 (AI-prompted coding): re-run the candidate's final code
    against the scenario's HR-approved test suite for an objective pass
    rate, then one LLM call judges the direction quality (precision,
    efficiency, independent judgment) against the full transcript."""
    scenario = submission.scenario
    reference = scenario.reference_json or {}
    test_cases = reference.get("test_cases", [])
    expected_approach = reference.get("expected_approach", "")

    turns = submission.round3_turns
    language = (submission.content or {}).get("language", "")
    final_code = next((t.code_after for t in reversed(turns) if t.code_after), None)

    if final_code is None:
        # No turn ever produced real code - nothing to run. See the
        # design spec's Scoring section: skip execution entirely rather
        # than attempting to run nothing.
        test_results = [{**tc, "actual_output": None, "passed": False} for tc in test_cases]
    else:
        test_results = []
        for tc in test_cases:
            # .get(), not tc["input"]/tc["expected_output"] directly:
            # defensive against a malformed reference (e.g. hand-edited
            # via HR's PATCH before/around Fix 2's validation landed) -
            # a missing key should fail this one test case with a clear
            # description, not blow up scoring with an opaque KeyError.
            tc_input = tc.get("input")
            if tc_input is None:
                test_results.append({
                    **tc,
                    "actual_output": None,
                    "passed": False,
                    "actual_output_error": "Reference test case is missing 'input' - could not run.",
                })
                continue
            result = execution_service.run_code(language=language, code=final_code, stdin=[tc_input])
            if result.infra_error:
                # Per the design spec's Error handling section: a hosted
                # execution-API infra failure (e.g. the Piston endpoint
                # rejecting the request) must never be scored as if it
                # were the candidate's own program crashing - it must
                # not be silently counted as a failed test case in the
                # coverage_score denominator. Raising here (rather than
                # recording actual_output=None/passed=False and
                # continuing) lets this propagate out uncaught, straight
                # into score_submission_in_background's existing
                # try/except below, which already routes any scoring-time
                # exception to RoundStatus.scoring_failed - the same path
                # every other scoring failure takes.
                raise RuntimeError(f"Code execution infra error while scoring test case: {tc.get('description', tc_input)}")
            actual_output = (result.stdout or "").strip()
            expected_output = tc.get("expected_output")
            # input()'s prompt argument (if the candidate's code passes
            # one) writes straight to stdout with no trailing newline
            # before whatever the program goes on to print - piped,
            # non-interactive stdin never echoes the typed value back,
            # so there is no separator between "prompt" and "the
            # program's real output" on the captured stream. The prompt
            # can only ever be a PREFIX of the real answer, never mixed
            # into it, so comparing the suffix is correct - an exact
            # match would fail every test case for any program that
            # prompts at all, which this round's own instructions
            # explicitly encourage. An empty expected_output is the one
            # case suffix-matching can't safely handle (every actual_output
            # trivially "ends with" ""), so that falls back to exact match.
            expected_stripped = str(expected_output).strip() if expected_output is not None else None
            if expected_stripped:
                passed = actual_output.endswith(expected_stripped)
            else:
                passed = expected_output is not None and actual_output == expected_stripped
            test_results.append({
                **tc,
                "actual_output": actual_output,
                "passed": passed,
            })

    conversation_payload = [t.to_conversation_payload() for t in turns]

    result = llm_service.score_round3_coding(
        scenario_description=scenario.description,
        expected_approach=expected_approach,
        conversation_so_far=conversation_payload,
        test_results=test_results,
    )

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    passed_count = sum(1 for r in test_results if r["passed"])
    score.coverage_score = round(passed_count / len(test_results) * 100) if test_results else 0
    score.test_results_json = test_results
    score.correctness_score = result.get("correctness_score")
    score.precision_score = result.get("precision_score")
    score.efficiency_score = result.get("efficiency_score")
    score.independent_judgment_score = result.get("independent_judgment_score")
    score.misses_json = result.get("misses", []) + [f"Guardrail: {g}" for g in result.get("guardrail_violations", [])]
    score.final_score = result.get("final_score")
    score.feedback_text = result.get("feedback_text")
    score.raw_llm_response_json = {"test_results": test_results, "conversation": conversation_payload, "scoring": result}
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


def _build_round4_evidence(submission: Submission) -> dict:
    """PRIMARY EVIDENCE for Round 4 scoring - see prompts/round4_scoring.txt's
    own PRIMARY EVIDENCE / REFERENCE ONLY labels and
    llm_service.score_round4_conversation's docstring, which this must
    stay in lockstep with. Built ENTIRELY from this one submission's own
    round4_test_cases/turns - no other round, no other candidate, no
    cached score ever contributes here. Keep this the only place that
    builds a round4_evidence dict, so "what counts as Round 4 evidence"
    has exactly one definition in the codebase."""
    test_cases = [
        {
            "title": tc.title or f"Test case {i + 1}",
            "turns": [
                {"turn_number": t.turn_number, "candidate_prompt": t.candidate_prompt, "model_response": t.model_response}
                for t in tc.turns
            ],
        }
        for i, tc in enumerate(submission.round4_test_cases)
    ]
    return {"test_cases": test_cases}


def _build_round1_reference_context(db: Session, submission: Submission) -> dict:
    """REFERENCE ONLY for Round 4 scoring - just enough of the
    candidate's own Round 1 work to understand the scenario being
    automated. Never a source of Round 4 findings (see the prompt's own
    guardrail language) - deliberately carries only scenario_title/
    scenario_description/submitted_rows, nothing that looks like a
    finding or a prior evaluation result.

    archived.is_(False) matters for a re-applied candidate (see
    CandidateAppearance) - without it, a candidate with more than one
    round 1 submission (current + an old, archived one from a prior
    cycle) could get scored against the WRONG scenario's title/
    description/rows, since .first() with no ordering has no guarantee
    of picking the current one. _round1_context_for (used for the live
    round 4 UI) already filters this correctly - this lookup, used at
    final scoring time, was the one place that didn't."""
    round1_submission = (
        db.query(Submission)
        .filter(Submission.user_id == submission.user_id, Submission.round_number == 1, Submission.archived.is_(False))
        .first()
    )
    return {
        "scenario_title": round1_submission.scenario.title if round1_submission else "",
        "scenario_description": round1_submission.scenario.description if round1_submission else "",
        "submitted_rows": (round1_submission.content or []) if round1_submission else [],
    }


def score_round4_submission(db: Session, submission: Submission) -> Score:
    """Round 4 gets one holistic score across all the candidate's own
    test cases (not per-test-case sub-scores - see
    llm_service.score_round4_conversation), same Score shape as every
    other round. Unlike rounds 1/2 there's no scenario reference to
    score against - the target is the candidate's own round 1
    submission, fetched fresh here rather than passed in.

    round4_evidence (PRIMARY) and round1_reference_context (REFERENCE
    ONLY) are built and passed as two separate, narrowly-shaped dicts -
    see _build_round4_evidence/_build_round1_reference_context and
    llm_service.score_round4_conversation's own enforced key whitelist -
    rather than one combined blob, specifically so Round 2, Round 3,
    another candidate's data, or a previous scoring result have no field
    to be accidentally placed into on either side of that boundary."""
    scenario = submission.scenario
    config = {**llm_service.DEFAULT_ROUND4_CONFIG, **(scenario.config_json or {})}

    round4_evidence = _build_round4_evidence(submission)
    round1_reference_context = _build_round1_reference_context(db, submission)

    result = llm_service.score_round4_conversation(
        round4_evidence=round4_evidence,
        round1_reference_context=round1_reference_context,
        assistance_pct=config["assistance_pct"],
    )
    misses, final_score, evidence_audit_summary = _round4_findings_to_misses(result, round4_evidence["test_cases"])

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.coverage_score = result.get("coverage_score")
    score.misses_json = misses
    score.final_score = final_score
    score.feedback_text = result.get("feedback_text")
    raw_response = {
        "round1_reference_context": round1_reference_context,
        "round4_evidence": round4_evidence,
        "scoring": result,
    }
    if evidence_audit_summary is not None:
        raw_response["evidence_audit"] = evidence_audit_summary
    score.raw_llm_response_json = raw_response
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


def _round4_findings_to_misses(
    result: dict, test_cases_payload: list[dict],
    supporting_texts: list[str] | dict[str, list[str]] | None = None,
) -> tuple[list[str], int | None, dict | None]:
    """Runs the scorer's structured findings (see prompts/round4_scoring.txt
    and schemas.Round4Finding) through the deterministic, non-LLM evidence
    auditor before any of them are allowed to become a scored weakness or
    move final_score - see round4_evidence_audit.py's module docstring for
    why a prompt telling the LLM "don't hallucinate" isn't sufficient on
    its own. A finding the auditor rejects (NOT_ESTABLISHED or
    CONTRADICTED) is dropped from misses_json entirely and its severity's
    points are added back to final_score, so an unsupported deduction
    never survives into the persisted Score.

    Backward compatible with the old flat `misses: [str, ...]` shape (no
    `findings` key), for tests that monkeypatch score_round4_conversation
    directly with that shape (e.g. test_round4.py's FAKE_SCORE) - those
    pass straight through with no audit, exactly as score_round4_submission
    behaved previously. A REAL call never takes this branch:
    llm_service.score_round4_conversation raises if its own LLM response
    is missing "findings" rather than returning the old shape, since the
    prompt it sends always asks for "findings" - so this is a test
    convenience, not a live fallback a real scoring result can silently
    slip through."""
    findings = result.get("findings")
    if findings is None:
        return result.get("misses", []), result.get("final_score"), None

    report = round4_evidence_audit.audit_round4_findings(test_cases_payload, findings, supporting_texts)
    final_score = result.get("final_score")
    if isinstance(final_score, (int, float)):
        final_score = min(100, int(final_score) + report.score_adjustment())
    # "findings" carries the full explainable chain per finding (Finding
    # -> Evidence -> Evidence status -> Score impact - see
    # FindingAudit.to_dict); the rest are the pre-existing aggregate
    # counts. Both land in Score.raw_llm_response_json["evidence_audit"],
    # HR-internal diagnostics only (see routers/hr.py - never surfaced to
    # a candidate).
    audit_report = {**report.summary(), "findings": report.findings_detail()}
    return report.surviving_claims(), final_score, audit_report


def _build_round4_pilot_evidence(submission: Submission) -> dict:
    """PRIMARY EVIDENCE for the Round 4 pilot ("Focused Automation
    Pilot") - built entirely from this submission's own content JSON
    (see routers/candidate.py's /round/4/pilot/* endpoints, the only
    writers of this shape - never from another submission or a cached
    score). Wraps the pilot's flat AI-turn list as ONE synthetic "test
    case" so round4_evidence_audit.py (built for the legacy per-test-case
    shape) can check evidence citations against it completely unmodified -
    the auditor only needs a flattened turn list with candidate_prompt/
    model_response per turn, not what the turns semantically represent."""
    content = submission.content or {}
    turns = content.get("turns") or []
    audit_turns = [
        {"turn_number": t["turn_number"], "candidate_prompt": t["candidate_prompt"], "model_response": t.get("response_message", "")}
        for t in turns
    ]
    return {
        "test_cases": [{"title": "Transaction flow automation", "turns": audit_turns}],
        "final_code": content.get("code", ""),
        "clarification": (
            {"question": content["clarification_question"], "response": content["clarification_response"]}
            if content.get("clarification_question") else None
        ),
        "execution_result": content.get("last_run") or {},
    }


def score_round4_pilot_submission(db: Session, submission: Submission) -> Score:
    """Scores a Round 4 pilot submission - see
    llm_service.score_round4_pilot_conversation and
    prompts/round4_pilot_scoring.txt's 5-area rubric. Findings go through
    the SAME round4_evidence_audit backstop as the legacy round 4 path
    (_round4_findings_to_misses, reused completely unmodified below) - a
    pilot finding with no real citation in the candidate's own turns/code
    never survives into the persisted Score any more than a legacy one
    does. Per-area sub-scores live inside raw_llm_response_json["scoring"]
    ["scores"] rather than new Score columns - see ScoreOut.evidence_audit's
    own reasoning for exposing structured detail through the existing
    raw_llm_response_json column instead of widening the Score table."""
    scenario = submission.scenario
    reference = scenario.reference_json or {}
    pilot_evidence = _build_round4_pilot_evidence(submission)

    result = llm_service.score_round4_pilot_conversation(
        scenario_instructions=scenario.description,
        starter_code=(scenario.config_json or {}).get("starter_code", ""),
        final_code=pilot_evidence["final_code"],
        turns=pilot_evidence["test_cases"][0]["turns"],
        clarification=pilot_evidence["clarification"],
        execution_result=pilot_evidence["execution_result"],
        reference_solution=reference.get("reference_solution", ""),
        validation_notes=reference.get("validation_notes", ""),
    )
    misses, final_score, evidence_audit_summary = _round4_findings_to_misses(result, pilot_evidence["test_cases"])

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.misses_json = misses
    score.final_score = final_score
    score.feedback_text = result.get("feedback_text")
    raw_response = {"pilot_evidence": pilot_evidence, "scoring": result}
    if evidence_audit_summary is not None:
        raw_response["evidence_audit"] = evidence_audit_summary
    score.raw_llm_response_json = raw_response
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


_TC_DESIGN_FIELDS = ("index", "title", "preconditions", "steps", "test_data", "expected_result", "refinements")


def _auto_tc_design_only(row: dict) -> dict:
    """Strips a selected row down to its immutable design fields only -
    the shape every selected row had before round 2 gained independent
    per-TC automation state (see routers/candidate.py's round4_auto_select).
    Used wherever "the candidate's design" is shown, so mutable state
    (code/turns/code_edits/last_run/validation) never leaks into what's
    supposed to be the immutable-design section of the scoring prompt.

    test_data reflects the candidate's own correction if they made one
    (see routers/candidate.py's round4_auto_update_test_data) - that's
    what the AI actually automated against and what the scorer should
    judge against, not a value the candidate has since flagged as wrong.
    The original is kept alongside as original_test_data whenever a
    correction exists, purely for transparency - never overwritten,
    never silently dropped."""
    design = {k: row[k] for k in _TC_DESIGN_FIELDS if k in row}
    override = row.get("test_data_override")
    if override:
        design["test_data"] = override
        design["original_test_data"] = row.get("test_data", "")
    return design


def _auto_tc_label(row: dict) -> str:
    """The exact per-test-case label shown both to the scorer (as each
    evidence block's own heading - see _auto_tc_evidence_blocks) and to
    the evidence auditor (_auto_tc_audit_payload's title;
    round4_evidence_audit's test_case matching for both the no_turns
    shape and the TC-scoped quote-only shape) - defined once so the two
    can never drift out of sync. Index-prefixed so it's unique even if
    two selected test cases share a candidate-authored title."""
    return f"Test case {row.get('index')}: {row.get('title', '')}".strip()


def _auto_tc_evidence_blocks(selected: list[dict]) -> list[dict]:
    """One self-contained evidence block per selected test case - no
    concatenation, no cross-TC label-parsing, replacing the old
    _aggregate_auto_tc_state flattening. Each block carries exactly what
    routers/candidate.py's round4_auto_turn/clarify/code/run/submit
    persist for that ONE test case: its own design (title/steps/
    test_data/expected_result AND its own append-only refinement notes -
    see _auto_tc_design_only, which already scopes "refinements" to this
    one row), its own final code, its own turns (including clarify turns
    - already distinguished from code-generating ones by response_kind,
    nothing extra needed here), its own code_edits, its own last
    execution result. No candidate-written interpretation field - whether
    a run genuinely proves the expected result is judged from final_code
    and execution_result alone (see prompts/round4_auto_scoring.txt).
    This round's scoring MODEL is not being redesigned - only the shape
    of the data feeding it, since it used to live in one shared buffer
    (then one flattened string) and now lives, and is shown, per selected
    test case. For exactly one selected test case this is still a
    lossless passthrough of that one test case's own state - every
    existing single-TC scoring expectation holds exactly as before, just
    one level more nested."""
    blocks = []
    for row in selected:
        blocks.append({
            "tc_index": row.get("index"),
            "label": _auto_tc_label(row),
            "design": _auto_tc_design_only(row),
            "final_code": row.get("code", ""),
            "turns": list(row.get("turns") or []),
            "code_edits": list(row.get("code_edits") or []),
            "execution_result": row.get("last_run") or {},
        })
    return blocks


def _auto_tc_audit_payload(selected: list[dict]) -> list[dict]:
    """One real per-test-case entry - not a single synthetic "Automation
    session" entry. round4_evidence_audit numbers a turn citation as a
    1-indexed position in the FLATTENED sequence across every entry in
    this list, in order (see that module's own docstring) - with one
    synthetic entry, two selected test cases' own turn 1 (each test
    case's stored turn_number restarts at 1 - see row.get("turns")) both
    occupied flattened position 1, so a citation naming one test case's
    turn could validate against the OTHER's turn text. Splitting into
    real per-test-case entries is exactly the shape the audit module was
    built for - proven already by round4_scoring.txt's legacy debugging
    flow, which uses the identical convention - so round4_evidence_audit.py
    itself needs no change for turn citations. The SAME label
    (_auto_tc_label) is also the key scoring_service.score_round4_auto_submission
    builds its TC-scoped supporting_texts dict with, so a quote-only
    citation tagged with this exact label gets checked against only that
    test case's own evidence too - see round4_evidence_audit._check_evidence.

    For exactly one selected test case this returns exactly one entry,
    so flattened position and that test case's own turn_number coincide
    - single-TC citation behavior is unchanged."""
    payload = []
    for row in selected:
        audit_turns = [
            {"turn_number": t["turn_number"], "candidate_prompt": t["candidate_prompt"], "model_response": t.get("response_message", "")}
            for t in (row.get("turns") or [])
        ]
        payload.append({"title": _auto_tc_label(row), "turns": audit_turns})
    return payload


def score_round4_auto_submission(db: Session, submission: Submission) -> Score:
    """AI-Assisted Test Automation - see llm_service.score_round4_auto_conversation
    and prompts/round4_auto_scoring.txt's 5-area rubric. Every piece of
    PRIMARY EVIDENCE comes from this submission's own content JSON (see
    routers/candidate.py's /round/4/auto/* endpoints, its only writers);
    ground truth and validation notes come from the scenario's
    reference_json and are REFERENCE ONLY. Findings run through the same
    round4_evidence_audit backstop (_round4_findings_to_misses, reused
    unmodified) as the other round 4 flows, and the per-area sub-scores
    live inside raw_llm_response_json rather than new Score columns -
    same reasoning as score_round4_pilot_submission.

    Each selected test case now carries its own independent state, sent
    to the scorer as its own self-contained evidence block (see
    _auto_tc_evidence_blocks) - no concatenated multi-TC code/turns/
    execution state crosses into the prompt. This function still makes
    exactly ONE scoring call per submission, same as before; only how
    its inputs are gathered and shaped changed, not the scoring model
    itself."""
    from . import round4_auto_policy

    scenario = submission.scenario
    reference = scenario.reference_json or {}
    content = submission.content or {}
    selected = content.get("selected") or []
    language = content.get("language", "python")
    tc_evidence = _auto_tc_evidence_blocks(selected)

    # Literal-invention checking stays scoped to the ONE test case a
    # literal could actually have come from - a candidate's design for
    # TC0 is not evidence for what's traceable in TC1's code, so this
    # runs per block rather than once over concatenated code/design.
    environments = (scenario.config_json or {}).get("environment_code_by_language") or {}
    environment_code = environments.get(language, "")
    for block in tc_evidence:
        block["untraceable_literals"] = round4_auto_policy.untraceable_literals(
            block["final_code"], [block["design"]], environment_code=environment_code,
        )

    # One real audit entry per selected test case - see
    # _auto_tc_audit_payload's docstring for why a single synthetic entry
    # let a citation naming one test case's turn validate against
    # another's.
    audit_payload = _auto_tc_audit_payload(selected)

    result = llm_service.score_round4_auto_conversation(
        language=language,
        tc_evidence=tc_evidence,
        ground_truth=reference.get("ground_truth", ""),
        validation_notes=reference.get("validation_notes", ""),
    )
    # This round's PRIMARY EVIDENCE is not only the conversation: the
    # rubric grades each TC's own final code and execution result too.
    # supporting_texts_by_tc keeps a quote-only citation scoped to the
    # ONE test case it's tagged with (evidence.test_case = that block's
    # own label) so a finding can't borrow evidence that only exists in a
    # different selected test case - see
    # round4_evidence_audit._check_evidence. An untagged or
    # unrecognized-tag citation still falls back to the lenient
    # whole-submission check below it, unchanged from before.
    supporting_texts_by_tc = {
        block["label"]: [
            block["final_code"],
            block["execution_result"].get("stdout", ""),
            block["execution_result"].get("stderr", ""),
        ]
        for block in tc_evidence
    }
    misses, final_score, evidence_audit_summary = _round4_findings_to_misses(
        result, audit_payload, supporting_texts_by_tc,
    )

    score = _get_or_create_score(db, submission)
    _apply_provenance(score, result)
    score.misses_json = misses
    score.final_score = final_score
    score.feedback_text = result.get("feedback_text")
    raw_response = {
        "tc_evidence": tc_evidence,
        "scoring": result,
    }
    if evidence_audit_summary is not None:
        raw_response["evidence_audit"] = evidence_audit_summary
    score.raw_llm_response_json = raw_response
    submission.status = RoundStatus.scored
    db.commit()
    db.refresh(score)
    return score


def _score_round4(db: Session, submission: Submission) -> Score:
    """Round 4 dispatch: a scenario's config_json.mode decides which of
    the two round 4 flows actually scores this submission - legacy
    conversation-based (score_round4_submission, unchanged) or the new
    pilot single-file-automation flow (score_round4_pilot_submission) -
    see routers/hr.py's publish_scenario for where that mode is set.
    Every pre-existing round 4 scenario has no "mode" key at all, so this
    is a 100%-additive branch: score_round4_submission's own behavior for
    every scenario that predates the pilot is completely unchanged."""
    mode = (submission.scenario.config_json or {}).get("mode")
    if mode == "ai_test_automation":
        return score_round4_auto_submission(db, submission)
    if mode == "pilot_automation":
        return score_round4_pilot_submission(db, submission)
    return score_round4_submission(db, submission)


# Slot -> scorer. The automation family (legacy conversational, pilot and
# AI-assisted automation - see _score_round4's own mode dispatch) now sits
# at slot 2, and the debugging investigation at slot 4; the FUNCTION names
# still carry the round numbers those flows were built under, kept as a
# historical label the same way this repo's migrate_round3_*.py scripts
# are. This dict is the single place that says which slot runs which flow.
_SCORERS = {
    1: score_round1_submission,
    2: _score_round4,                 # AI-Assisted Test Automation
    3: score_round3_submission,
    4: score_round2_investigation,    # Debugging
}


def score_submission_in_background(submission_id: int) -> None:
    """The one entry point every submit endpoint's BackgroundTasks call
    and HR's retry-scoring endpoint (routers/hr.py) both go through -
    consolidates what used to be three near-identical, exception-unsafe
    _score_roundN_in_background functions in routers/candidate.py.

    Never lets a failed LLM call strand a submission at status="submitted"
    forever with nothing to show for it: on any exception (a bad LLM
    response, a network blip, a rate limit, ...) the submission moves to
    scoring_failed with the error message attached, visible to HR, who
    can retry (call this function again) or score it manually (see
    hr.py's PATCH /submissions/{id}/score)."""
    from ..database import SessionLocal  # local import: avoid circular import at module load

    db = SessionLocal()
    try:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        scorer = _SCORERS.get(submission.round_number)
        if scorer is None:
            return
        # Clear any stale error from a previous failed attempt before
        # trying again - a successful retry must not leave old error
        # text sitting next to a fresh, real score.
        submission.scoring_error = None
        try:
            scorer(db, submission)
            # A fresh score means the candidate's cross-round HR summary
            # (routers/hr.py's POST/GET .../summary) is now describing a
            # superseded attempt - drop it rather than let HR read a
            # verdict built from a round the candidate has since retaken.
            # They regenerate on demand (same endpoint); nothing here
            # calls the LLM.
            db.query(CandidateSummary).filter(CandidateSummary.user_id == submission.user_id).delete()
        except Exception as e:
            db.rollback()  # discard any half-formed pending changes (e.g. a Score added but not yet committed) before recording the failure
            submission.status = RoundStatus.scoring_failed
            submission.scoring_error = str(e)[:2000]
            db.commit()
    finally:
        db.close()


def finalize_abandoned_submission(submission: Submission, reason: str, submitted_at: datetime) -> None:
    """Ends an in_progress submission that has no path back to a real
    submit - either it timed out (close_expired_submissions below) or it
    was archived out from under the candidate by a re-upload/reset
    (candidate_upload_service._reset_and_archive), which removes it from
    every "current submission" lookup just as permanently as a deadline
    would. The round-specific content default is the only part that
    actually differs from a real submit, and it's identical in both
    cases. Caller is responsible for db.commit() and scheduling scoring."""
    if submission.round_number == 1:
        submission.content = submission.content or []
    elif submission.round_number == 4:
        # Debugging (slot 4 since the 2<->4 renumbering) - its content is
        # investigation-shaped.
        submission.content = submission.content or {"investigation": [], "root_cause": ""}
    # The automation round (slot 2) has no content default to set here -
    # its state already lives in round4_test_cases/conversation_turns, or
    # in its own content JSON for the pilot/auto modes; whatever exists
    # (including none) is what gets scored.
    submission.status = RoundStatus.submitted
    submission.submitted_at = submitted_at
    submission.auto_closed_reason = reason


def close_expired_submissions(db: Session, submissions: list[Submission], background_tasks: BackgroundTasks) -> list[Submission]:
    """The server-side counterpart to POST /round/{n}/expire (routers/
    candidate.py), for when nobody's browser was ever there to call it -
    a closed tab, a crash, a logout, network loss, anything. That
    endpoint only fires because the candidate's OWN timer hit zero while
    their page was open; if they're simply gone, their submission would
    otherwise sit at in_progress forever, with nothing to ever close it
    out. Called lazily at the points where a stale in_progress submission
    actually matters - HR's time-limit edit block, HR's candidate views,
    the candidate's own round-gating checks if they ever come back - so
    it self-heals the moment anything looks at it, no cron job needed.

    Same principle as /expire: whatever content exists (possibly none,
    since nothing server-side ever saw draft rows a candidate never
    submitted for rounds 1/2) becomes the final submission, scored
    normally - "didn't attempt it" is a scoring outcome, not a special
    case. auto_closed_reason distinguishes this from a normal submit for
    HR, without changing what gets scored.

    A deliberate logout (the Logout button) now ends the round
    immediately instead - see routers/auth.py's logout, which calls
    finalize_abandoned_submission directly the moment it happens, not
    lazily. This deadline-based path is what's left to catch everyone
    who never clicks that button at all: a closed tab, a crash, lost
    network, lost power - anything that leaves a round in_progress with
    nobody ever explicitly ending it."""
    now = datetime.utcnow()
    closed = []
    for submission in submissions:
        if submission.status != RoundStatus.in_progress or submission.started_at is None:
            continue
        scenario = submission.scenario
        deadline = submission.started_at + timedelta(minutes=scenario.time_limit_minutes)
        if now < deadline:
            continue

        # The round's own deadline, not `now` - this check can run
        # arbitrarily later than the actual expiry (whenever HR next
        # views the dashboard), and `now` would misrepresent "when the
        # candidate finished" as that later moment instead of when time
        # genuinely ran out.
        finalize_abandoned_submission(submission, "Time limit reached without a manual submit", deadline)
        db.commit()
        db.refresh(submission)
        closed.append(submission)

    # Scheduled via BackgroundTasks, same as every submit endpoint
    # (submit_round/submit_round2/round4_submit/expire_round) - a direct
    # call here would block whichever request happened to be the one
    # that lazily closed this submission (e.g. HR's /candidates
    # dashboard, which can lazily close several abandoned submissions in
    # one request) on a real LLM call before its response can be sent.
    # Each score_submission_in_background call is already its own
    # exception-safe, self-contained unit (own session, own commit), so
    # scheduling them all after the loop above (not while looping/
    # committing) just keeps this batch's own DB state simple to reason
    # about - it doesn't affect isolation between the scoring calls
    # themselves.
    for submission in closed:
        background_tasks.add_task(score_submission_in_background, submission.id)

    return closed


def close_expired_assessment_windows(
    db: Session, candidate: User, app_settings: AppSettings, background_tasks: BackgroundTasks,
) -> list[Submission]:
    """The one-level-earlier counterpart to close_expired_submissions
    above: that function can only ever act on a round that's already
    in_progress - it needs a started_at to compute a deadline from. A
    round the candidate never even started has no started_at, so nothing
    about per-round timeouts can ever resolve it on its own.
    "not_started" is a state with no maximum residency at all without
    this - a candidate who simply never begins the next round sits there
    indefinitely, with nothing forcing a resolution.

    Anchored to AppSettings.assessment_window_days from whichever the
    candidate actually has: round 1's own Submission.started_at, if
    they've begun at all, or their current CandidateAppearance.exam_date
    as a fallback for the genuine no-show case (scheduled, never showed
    up at all - round 1 itself has no started_at yet either, and gets
    closed out below same as every other not-yet-started round). This
    used to be exam_date-only - anchored purely to bulk-uploaded
    candidates' scheduling data - which meant any candidate without a
    CandidateAppearance (every seeded/demo account, or any future
    creation path that isn't the bulk-upload flow) got NO enforcement at
    all, silently. started_at is universal: every candidate gets one the
    instant they click Start, regardless of how their account was
    created, so anchoring there first closes the gap for everyone, not
    just one creation path. Only truly exempt now: an account that has
    neither ever started round 1 nor has any exam_date to fall back on -
    nothing has happened yet to measure a deadline from, which is a
    different (and harmless) state than "started something, then stalled."
    """
    round1_submission = next(
        (s for s in candidate.submissions if s.round_number == 1 and not s.archived and s.started_at is not None),
        None,
    )
    appearance = next((a for a in candidate.appearances if a.is_current), None)
    if round1_submission is not None:
        anchor = round1_submission.started_at
    elif appearance is not None:
        anchor = appearance.exam_date
    else:
        return []
    deadline = anchor + timedelta(days=app_settings.assessment_window_days)
    if datetime.utcnow() < deadline:
        return []

    existing_rounds = {s.round_number for s in candidate.submissions if not s.archived}
    closed = []
    for round_number in _ASSESSMENT_ROUND_SEQUENCE:
        if round_number in existing_rounds:
            continue
        scenario = (
            db.query(Scenario)
            .filter(
                Scenario.round_number == round_number,
                Scenario.experience_band == candidate.experience_band,
                Scenario.is_live.is_(True),
            )
            .first()
        )
        if scenario is None:
            continue  # nothing published/live for this round+band - leave it "not_started", there's nothing to score against
        if round_number == 1:
            content = []
        elif round_number == 4:
            content = {"investigation": [], "root_cause": ""}  # debugging (slot 4 since the 2<->4 renumbering)
        else:
            content = None  # rounds 2/3 keep their state in their own tables/content JSON, same as a real submission would
        submission = Submission(
            user_id=candidate.id, scenario_id=scenario.id, round_number=round_number,
            started_at=deadline, status=RoundStatus.submitted, submitted_at=deadline,
            auto_closed_reason="Assessment window closed before this round was ever started",
            appearance_id=appearance.id if appearance else None, content=content,
        )
        db.add(submission)
        closed.append(submission)

    if closed:
        db.commit()
        for submission in closed:
            db.refresh(submission)
            background_tasks.add_task(score_submission_in_background, submission.id)
    return closed
