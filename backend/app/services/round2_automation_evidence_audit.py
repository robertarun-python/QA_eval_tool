"""
Deterministic (no LLM) verification of Round 2 automation scoring
findings against the actual transcript (built for the retired
conversational format, still named for its old slot). The scorer
(prompts/round2_automation_scoring.txt) already carries strong evidence-discipline instructions, but a prompt is
not a guarantee - this module is the backstop: it independently checks
whether each finding's cited evidence actually exists in the transcript
before that finding is allowed to cost the candidate any points. It
never calls an LLM; every verdict here is produced by exact/normalized
string matching and structural counting against test_cases_json, the
same data the scorer was given.

Turn numbering: schemas.Round2AutomationFindingEvidence.turn is a 1-indexed
position in the FLATTENED sequence of every turn across every test
case, in the same order test cases appear in the scoring payload (test
case 1's turns, then test case 2's, ...) - not the per-test-case
turn_number used elsewhere in this app (see
scoring_service._auto_tc_audit_payload, which builds the payload this way).

See scoring_service._round2_automation_findings_to_misses for how an AuditReport's
surviving_claims()/score_adjustment() feed back into the persisted Score.
"""
import re
import unicodedata
from dataclasses import dataclass, field

# ponytail: three fixed point values, not a config knob - this is an
# internal scoring-consistency mechanism (undoing a rejected finding's
# implied deduction), not a product-tunable setting. Revisit if HR ever
# asks to tune how much a rejected finding should give back.
SEVERITY_WEIGHTS = {"low": 3, "medium": 8, "high": 15}

_WHITESPACE_RE = re.compile(r"\s+")
_QUOTE_TRANSLATION = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
})

# Claims phrased as an absolute negative ("zero follow-up", "never
# verified") are the exact failure mode this module exists to catch -
# see the module docstring's Welcome-label example and CONTRADICTED
# below. Matched independently of whatever evidence the finding cites,
# so a plausible-looking but wrong quote can't smuggle the claim through.
_ABSOLUTE_NEGATIVE_CLAIM = re.compile(
    r"zero follow-?up|no follow-?up|without (any )?(investigation|verification|question)|"
    r"never (asked|questioned|challenged|verified|re-?ran|re-?checked|investigated)|"
    r"accepted\b.*\bwithout\b",
    re.IGNORECASE,
)

# What counts as the candidate actually following up, for the check
# above - a literal question mark, or one of the common phrasings this
# app's own transcripts use to challenge an unexpected result (see
# tests/test_round2_automation_setup.py fixtures and the real double-booking transcript
# this module was written against). Deliberately narrow: a false
# negative here just leaves a finding at whatever its evidence-based
# status already was, never wrongly downgrades a genuinely valid finding.
_FOLLOW_UP_INDICATOR = re.compile(
    r"\?|shouldn'?t|should( n't| not)? (have|it)|that('?s| is) (wrong|odd|strange|not right)|"
    r"expected .* but|why (did|does|is|would)|doesn'?t seem right|correct this|"
    r"re-?run|re-?check|verify (this|that|again)|double[- ]check",
    re.IGNORECASE,
)


def _normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_TRANSLATION)
    return _WHITESPACE_RE.sub(" ", text).strip()


# The scorer is shown each evidence block as json.dumps text, so it sometimes quotes a value with
# its JSON escapes still in (\" for a quote, \n for a line break) - or a mix of escaped and real
# characters within one quote (seen live: an escaped \" next to a real line break), which then
# matched neither the raw run output nor its JSON form. One left-to-right pass, so an escaped
# backslash (\\) followed by n stays a backslash and an n.
_JSON_ESCAPE_RE = re.compile(r'\\(["\\/nrt])')
_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "r": "\n", "t": "\t"}


def _evidence_form(text: str | None) -> str:
    """The form a cited quote and the recorded evidence are compared in: JSON escapes undone,
    then _normalize (line endings and runs of whitespace become one space). Applied to both
    sides of a containment check, never to anything else - the quote still has to be found
    inside the evidence it cites."""
    if not text:
        return ""
    return _normalize(_JSON_ESCAPE_RE.sub(lambda m: _JSON_ESCAPES[m.group(1)], text))


def _turn_text(turn: dict) -> str:
    """Every piece of literal text a human (or the scorer) could actually
    read for one turn - candidate_prompt plus every string field of
    model_response - concatenated for substring/containment checks."""
    parts = [turn.get("candidate_prompt") or ""]
    model_response = turn.get("model_response") or {}
    if isinstance(model_response, dict):
        parts.append(model_response.get("response_text") or "")
        parts.append(model_response.get("observed_result") or "")
        for step in model_response.get("steps") or []:
            if isinstance(step, dict):
                parts.append(step.get("description") or "")
                parts.append(step.get("detail") or "")
    else:
        parts.append(str(model_response))
    return _normalize(" ".join(p for p in parts if p))


def _flatten(test_cases: list[dict]) -> list[dict]:
    """[{"test_case": title, "raw": turn_dict}, ...] in payload order -
    see the module docstring's Turn numbering note."""
    flat = []
    for tc in test_cases or []:
        title = tc.get("title") or ""
        for turn in tc.get("turns") or []:
            flat.append({"test_case": title, "raw": turn})
    return flat


@dataclass
class EvidenceCheck:
    valid: bool
    reason: str  # "ok" | "invalid_turn" | "quote_not_found" | "unknown_test_case" | "turns_exist" | "missing_quote"


@dataclass
class FindingAudit:
    """One finding's full explainable chain: Finding -> Evidence ->
    Evidence status -> Score impact. `evidence` is the LLM's own raw
    citations (as given, unmodified) so the original claim can always be
    re-inspected; `evidence_checks` is this module's per-citation verdict
    on each of them."""
    claim: str
    severity: str
    # candidate_weakness: "SUPPORTED" | "NOT_ESTABLISHED" | "CONTRADICTED" | "SUPERSEDED_BY_DEFECT"
    # defect_detected:    "VERIFIED_DEFECT" | "UNVERIFIED_DETECTION"
    status: str
    evidence: list[dict] = field(default_factory=list)
    evidence_checks: list[EvidenceCheck] = field(default_factory=list)
    kind: str = "candidate_weakness"
    defect_id: str | None = None
    reason: str | None = None  # why a detection wasn't verified, or which detection superseded a weakness

    @property
    def is_weakness(self) -> bool:
        return self.kind != "defect_detected"

    @property
    def score_impact(self) -> int:
        """Points restored to the candidate because this finding's
        implied deduction was voided (0 for a finding that stands - see
        AuditReport.score_adjustment, which is just the sum of these).
        A detection never carried a deduction, so it never restores one."""
        return SEVERITY_WEIGHTS.get(self.severity, 0) if self.is_weakness and self.status != "SUPPORTED" else 0

    def to_dict(self) -> dict:
        out = {
            "finding": self.claim,
            "severity": self.severity,
            "evidence_status": self.status,
            "evidence": self.evidence,
            "score_impact": self.score_impact,
        }
        if not self.is_weakness:
            out.update(kind=self.kind, defect_id=self.defect_id)
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass
class AuditReport:
    findings: list[FindingAudit]

    def summary(self) -> dict:
        invalid_references = sum(
            1 for f in self.findings for c in f.evidence_checks if c.reason == "invalid_turn"
        )
        out = {
            "total_findings": len(self.findings),
            "supported": sum(1 for f in self.findings if f.status == "SUPPORTED"),
            "not_established": sum(1 for f in self.findings if f.status == "NOT_ESTABLISHED"),
            "contradicted": sum(1 for f in self.findings if f.status == "CONTRADICTED"),
            "invalid_references": invalid_references,
        }
        # Only present when a scenario declares known defects and the scorer used them.
        for key, status in (("superseded_by_defect", "SUPERSEDED_BY_DEFECT"), ("verified_defects", "VERIFIED_DEFECT"),
                            ("unverified_detections", "UNVERIFIED_DETECTION")):
            count = sum(1 for f in self.findings if f.status == status)
            if count:
                out[key] = count
        return out

    def findings_detail(self) -> list[dict]:
        """The full explainable chain for every finding, in order -
        Finding -> Evidence -> Evidence status -> Score impact - meant to
        be persisted verbatim (see scoring_service.score_round2_automation_submission's
        raw_llm_response_json["evidence_audit"]) so a rejected or accepted
        deduction can always be traced back to exactly what was cited and why."""
        return [f.to_dict() for f in self.findings]

    def surviving_claims(self) -> list[str]:
        """Claim text for every finding that actually cleared the audit -
        this, not the LLM's raw findings list, is what becomes Score.misses_json."""
        return [f.claim for f in self.findings if f.is_weakness and f.status == "SUPPORTED"]

    def score_adjustment(self) -> int:
        """Points to add back to the LLM's final_score - one rejected
        finding's implied deduction, reversed, per finding rejected. See
        scoring_service._round2_automation_findings_to_misses; this is what makes
        requirement 8 (an unsupported deduction must not survive) hold
        even though the LLM computes final_score as one holistic number
        rather than a literal running total. The raw upper bound - the
        Round 2 automation score uses bounded_adjustment."""
        return sum(f.score_impact for f in self.findings)

    def bounded_adjustment(self, base_score: int) -> int:
        """The refund actually applied to a 0-100 score: never more than the rejected
        findings' own weights, and never more than their proportional share of the
        deduction the scorer actually made (100 - base_score), so rejecting a finding can
        only undo part of a real deduction - never lift a score past it (candidate A:
        3 x 15 refunded on a 62 used to give 100). 0 when nothing was rejected."""
        rejected = sum(SEVERITY_WEIGHTS.get(f.severity, 0) for f in self.findings if f.score_impact)
        if not rejected:
            return 0
        supported = sum(SEVERITY_WEIGHTS.get(f.severity, 0) for f in self.findings if f.is_weakness and f.status == "SUPPORTED")
        deduction = max(0, 100 - base_score)
        return min(rejected, round(deduction * rejected / (supported + rejected)))

    def defects_detected(self) -> list[dict]:
        """Verified detections of known application defects - HR-only, never misses."""
        return [{"defect_id": f.defect_id, "finding": f.claim, "evidence": f.evidence}
                for f in self.findings if f.status == "VERIFIED_DEFECT"]

    def unverified_detections(self) -> list[dict]:
        return [{"defect_id": f.defect_id, "finding": f.claim, "reason": f.reason}
                for f in self.findings if f.status == "UNVERIFIED_DETECTION"]


def _check_evidence(
    evidence: dict, flat: list[dict], turn_texts: list[str], turn_counts: dict[str, int],
    supporting_texts: list[str] | None = None, supporting_texts_by_tc: dict[str, list[str]] | None = None,
    evidence_text_by_tc: dict[str, list[str]] | None = None,
) -> EvidenceCheck:
    # evidence_text_by_tc: each test case's whole evidence block exactly as the scorer was
    # shown it (design, code_edits, execution_result incl. exit_code, ...). A quote the
    # scorer copied from any of it is real recorded evidence - candidate A's true findings
    # quoted "exit_code": 1 and "code_edits": [] and were wrongly refunded because only
    # final_code/stdout/stderr were searched. Same verbatim matching, same TC scoping.
    evidence_text_by_tc = evidence_text_by_tc or {}
    # A quote that appears verbatim in one of the caller's OTHER persisted
    # evidence artefacts (see audit_round2_automation_findings' supporting_texts) is
    # established the same way a turn quote is: the text demonstrably
    # exists in something this submission actually recorded. Checked before
    # the turn lookup because a finding about the final code or the
    # execution output has no meaningful turn number to cite. Callers that
    # pass nothing here are completely unaffected.
    quote_text = evidence.get("quote")
    # Only a genuinely quote-only citation (no turn, not the no_turns
    # shape) is eligible for TC-scoped matching below - tagging a real
    # turn citation with test_case isn't a shape this module's callers
    # ever produce, and scoping a turn citation by it would be meaningless.
    is_quote_only = quote_text is not None and evidence.get("turn") is None and not evidence.get("no_turns")

    if is_quote_only and _evidence_form(quote_text):
        needle = _evidence_form(quote_text)
        # TC-scoped check, when the caller supplied per-test-case evidence
        # (supporting_texts_by_tc, keyed the same way
        # _auto_tc_audit_payload/round2_automation_scoring.txt label a test
        # case) AND the finding tagged which one this quote is about.
        # Multi-TC submissions only - see scoring_service.
        # _auto_tc_evidence_blocks. A recognized tag that ISN'T found
        # there is a confirmed cross-test-case misattribution - reject
        # outright, do NOT fall through to the lenient whole-submission
        # check below, or a quote genuinely from a DIFFERENT selected
        # test case's evidence could validate a finding that claims this
        # one. An unrecognized/missing tag (single-TC submissions,
        # legacy callers, or a model that didn't tag it) falls through
        # unchanged - this feature only ever ADDS strictness, it never
        # removes the existing lenient behavior below.
        tc_label = evidence.get("test_case")
        if supporting_texts_by_tc and tc_label and tc_label in supporting_texts_by_tc:
            if any(needle in text for text in supporting_texts_by_tc[tc_label]):
                return EvidenceCheck(True, "ok_supporting_evidence")
            if any(needle in text for text in evidence_text_by_tc.get(tc_label, [])):
                return EvidenceCheck(True, "ok_evidence_package")
            return EvidenceCheck(False, "wrong_test_case")

    # Unchanged from before this feature existed: a quote that appears
    # verbatim in one of the caller's OTHER persisted evidence artefacts
    # is established the same way a turn quote is - the text demonstrably
    # exists in something this submission actually recorded. Runs
    # regardless of is_quote_only (preserves the exact original condition
    # - any quote-bearing citation, not only quote-only ones) so every
    # existing caller's behavior is untouched byte-for-byte.
    if supporting_texts and quote_text and _evidence_form(quote_text):
        needle = _evidence_form(quote_text)
        if any(needle in text for text in supporting_texts):
            return EvidenceCheck(True, "ok_supporting_evidence")
    if is_quote_only and _evidence_form(quote_text):
        needle = _evidence_form(quote_text)
        if any(needle in text for texts in evidence_text_by_tc.values() for text in texts):
            return EvidenceCheck(True, "ok_evidence_package")

    if evidence.get("no_turns"):
        title = evidence.get("test_case") or ""
        if title not in turn_counts:
            return EvidenceCheck(False, "unknown_test_case")
        if turn_counts[title] == 0:
            return EvidenceCheck(True, "ok")
        # The evaluator claimed this test case has no turns - it does.
        # This is direct, structural proof of the opposite, not mere
        # silence - see the module docstring and rule 6 in the spec this
        # module implements (proves-it-didn't-happen vs. doesn't-show-it).
        return EvidenceCheck(False, "turns_exist")

    turn_no = evidence.get("turn")
    quote = evidence.get("quote")
    if not isinstance(turn_no, int) or turn_no < 1 or turn_no > len(flat):
        return EvidenceCheck(False, "invalid_turn")
    if not quote or not _evidence_form(quote):
        return EvidenceCheck(False, "missing_quote")
    if _evidence_form(quote) in turn_texts[turn_no - 1]:
        return EvidenceCheck(True, "ok")
    return EvidenceCheck(False, "quote_not_found")


def _contradicted_by_followup(claim: str, evidence: list[dict], flat: list[dict]) -> bool:
    """Independent of whatever evidence a finding cites: if the claim
    itself asserts an absolute absence of follow-up/verification, scan
    the actual candidate prompts (scoped to a named test case when the
    finding's evidence names one, otherwise the whole session) for a
    turn that plainly challenges/re-checks a result. Finding one is
    direct proof the absolute claim is false - see rule 8/17 in the
    evidence-audit spec this module implements."""
    if not _ABSOLUTE_NEGATIVE_CLAIM.search(claim):
        return False
    scoped_titles = {e.get("test_case") for e in evidence if e.get("test_case")}
    for item in flat:
        if scoped_titles and item["test_case"] not in scoped_titles:
            continue
        prompt_text = item["raw"].get("candidate_prompt") or ""
        if _FOLLOW_UP_INDICATOR.search(prompt_text):
            return True
    return False


def _verify_detection(
    defect_id: str | None, evidence: list[dict], checks: list[EvidenceCheck], known: dict[str, dict],
    execution_text_by_tc: dict[str, str], target_text_by_tc: dict[str, str],
) -> tuple[bool, str, list[str]]:
    """A "defect_detected" finding earns its status only when ALL hold: it names a declared,
    creditable known defect; its evidence is real; at least one cited quote comes from a test
    case's own run output AND shows that defect's observable signal; and that same test case's
    design or the candidate's own prompts targeted the affected behaviour. Seeing a failure,
    naming a defect, or the defect merely existing earns nothing. Returns (verified, reason,
    [(test case label, matched execution quote), ...])."""
    entry = known.get(defect_id or "")
    if entry is None:
        return False, "unknown_defect", []
    if not entry.get("creditable", False):
        return False, "not_creditable", []  # e.g. an expected value only the operator knows
    if not evidence or not all(c.valid for c in checks):
        return False, "evidence_not_established", []
    signals = [_normalize(s).lower() for s in entry.get("observable_signals") or [] if _normalize(s)]
    targets = [_normalize(t).lower() for t in entry.get("targets") or [] if _normalize(t)]
    if not signals or not targets:
        return False, "defect_not_fully_declared", []
    shown_in = []
    for ev in evidence:
        quote = _normalize(ev.get("quote")).lower()
        if not quote:
            continue
        labels = [ev["test_case"]] if ev.get("test_case") in execution_text_by_tc else list(execution_text_by_tc)
        for label in labels:
            if quote in execution_text_by_tc[label] and any(s in quote for s in signals):
                shown_in.append((label, quote))
    if not shown_in:
        return False, "not_shown_in_run_output", []
    targeted = [(label, q) for label, q in shown_in if any(t in target_text_by_tc.get(label, "") for t in targets)]
    if not targeted:
        return False, "test_did_not_target_behaviour", []
    return True, "", targeted


def audit_round2_automation_findings(
    test_cases: list[dict], findings: list[dict],
    supporting_texts: list[str] | dict[str, list[str]] | None = None,
    evidence_text_by_tc: dict[str, list[str]] | None = None,
    known_defects: list[dict] | None = None,
    execution_text_by_tc: dict[str, str] | None = None,
    target_text_by_tc: dict[str, str] | None = None,
) -> AuditReport:
    """The single entry point: check every LLM-generated finding against
    the transcript and return a verdict per finding, deterministically.
    `test_cases` is the per-test-case payload the scorer was given (see
    scoring_service._auto_tc_audit_payload);
    `findings` is the scorer's own findings list (list of dicts shaped
    like schemas.Round2AutomationFinding).

    `supporting_texts` is for a round whose PRIMARY EVIDENCE is not only a
    conversation. The AI-assisted automation round also persists a final
    code file, an execution result and the candidate's own written
    interpretation, and its rubric explicitly grades those - but a finding
    about any of them cites nothing that exists in the turn list, so it
    was being marked NOT_ESTABLISHED and its deduction silently refunded.
    Passing those already-persisted artefacts here lets such a finding be
    established the same way a turn quote is: the quote must appear
    verbatim in real recorded evidence. It does NOT weaken the model - a
    fabricated quote still matches nothing and stays unestablished - and
    callers that omit it (the legacy round 4 and pilot flows) behave
    exactly as before.

    Two shapes are accepted. A plain list is the original, whole-submission
    pool (unchanged behavior for every existing caller). A dict, keyed by
    the same test-case label _auto_tc_audit_payload uses for `test_cases`
    entries, additionally lets a quote-only citation be checked against
    ONLY the test case it's tagged as being about (see _check_evidence) -
    for a round where more than one test case's own code/execution/
    interpretation could otherwise be confused with another's. A finding
    with no recognizable tag still falls back to the flattened union of
    every test case's texts, so this is purely additive strictness."""
    flat = _flatten(test_cases)
    # Evidence texts in _evidence_form, the form _check_evidence compares quotes in.
    turn_texts = [_evidence_form(_turn_text(item["raw"])) for item in flat]
    turn_counts = {(tc.get("title") or ""): len(tc.get("turns") or []) for tc in test_cases or []}
    if isinstance(supporting_texts, dict):
        support_by_tc = {
            str(label): [_evidence_form(t) for t in (texts or []) if t and _evidence_form(t)]
            for label, texts in supporting_texts.items()
        }
        normalized_support = [text for texts in support_by_tc.values() for text in texts]
    else:
        support_by_tc = {}
        normalized_support = [_evidence_form(t) for t in (supporting_texts or []) if t and _evidence_form(t)]

    package_by_tc = {
        str(label): [_evidence_form(t) for t in (texts or []) if t and _evidence_form(t)]
        for label, texts in (evidence_text_by_tc or {}).items()
    }
    known = {str(d.get("id")): d for d in known_defects or [] if isinstance(d, dict) and d.get("id")}
    execution_by_tc = {str(k): _normalize(v).lower() for k, v in (execution_text_by_tc or {}).items()}
    target_by_tc = {str(k): _normalize(v).lower() for k, v in (target_text_by_tc or {}).items()}

    audited = []
    detection_quotes: list[tuple[str, str, str]] = []  # (defect_id, test case label, execution quote) of verified detections
    for raw in findings or []:
        claim = (raw.get("claim") or "").strip()
        severity = raw.get("severity") if raw.get("severity") in SEVERITY_WEIGHTS else "low"
        evidence = raw.get("evidence") or []
        kind = raw.get("kind") if raw.get("kind") == "defect_detected" else "candidate_weakness"

        if not evidence:
            status = "NOT_ESTABLISHED"
            checks: list[EvidenceCheck] = []
        else:
            checks = [_check_evidence(ev, flat, turn_texts, turn_counts, normalized_support, support_by_tc, package_by_tc)
                      for ev in evidence]
            if any(c.reason == "turns_exist" for c in checks):
                status = "CONTRADICTED"
            elif all(c.valid for c in checks):
                status = "SUPPORTED"
            else:
                status = "NOT_ESTABLISHED"

        if kind == "defect_detected":
            verified, reason, quotes = _verify_detection(raw.get("defect_id"), evidence, checks, known, execution_by_tc, target_by_tc)
            detection_quotes += [(raw.get("defect_id"), label, q) for label, q in quotes]
            audited.append(FindingAudit(claim=claim, severity=severity, status="VERIFIED_DEFECT" if verified else "UNVERIFIED_DETECTION",
                                        evidence=evidence, evidence_checks=checks, kind=kind,
                                        defect_id=raw.get("defect_id"), reason=reason or None))
            continue

        if status != "CONTRADICTED" and _contradicted_by_followup(claim, evidence, flat):
            status = "CONTRADICTED"

        audited.append(FindingAudit(claim=claim, severity=severity, status=status, evidence=evidence, evidence_checks=checks))

    # No double penalty: a supported weakness that rests ONLY on the verified defect's own failure
    # output is the application's fault, not the candidate's - every quote it cites must come
    # from the run output of the test case where that defect was verified (and not be tagged with
    # another test case) AND contain one of that defect's declared failure signals. Claim wording
    # and quote overlap alone never cancel anything. It stops being a miss and its deduction is
    # undone like any other rejected one.
    verified_at = {(d, label) for d, label, _ in detection_quotes}
    for f in audited:
        if not (f.is_weakness and f.status == "SUPPORTED" and f.evidence):
            continue
        for d, label in sorted(verified_at):
            signals = [_normalize(s).lower() for s in known[d].get("observable_signals") or [] if _normalize(s)]
            if all(_normalize(ev.get("quote")).lower()
                   and ev.get("test_case") in (None, "", label)
                   and _normalize(ev.get("quote")).lower() in execution_by_tc.get(label, "")
                   and any(s in _normalize(ev.get("quote")).lower() for s in signals)
                   for ev in f.evidence):
                f.status, f.reason = "SUPERSEDED_BY_DEFECT", f"rests only on verified {d}'s failure output"
                break

    return AuditReport(findings=audited)
