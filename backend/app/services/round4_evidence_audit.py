"""
Deterministic (no LLM) verification of Round 4 scoring findings against
the actual transcript. The Round 4 scorer (prompts/round4_scoring.txt)
already carries strong evidence-discipline instructions, but a prompt is
not a guarantee - this module is the backstop: it independently checks
whether each finding's cited evidence actually exists in the transcript
before that finding is allowed to cost the candidate any points. It
never calls an LLM; every verdict here is produced by exact/normalized
string matching and structural counting against test_cases_json, the
same data the scorer was given.

Turn numbering: schemas.Round4FindingEvidence.turn is a 1-indexed
position in the FLATTENED sequence of every turn across every test
case, in the same order test cases appear in the scoring payload (test
case 1's turns, then test case 2's, ...) - not the per-test-case
turn_number used elsewhere in this app (see ConversationTurn.turn_number
and round4_scoring.txt's evidence-discipline section, which instructs
the scorer to count this way).

See scoring_service.score_round4_submission for how an AuditReport's
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
# tests/test_round4.py fixtures and the real double-booking transcript
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
    status: str  # "SUPPORTED" | "NOT_ESTABLISHED" | "CONTRADICTED"
    evidence: list[dict] = field(default_factory=list)
    evidence_checks: list[EvidenceCheck] = field(default_factory=list)

    @property
    def score_impact(self) -> int:
        """Points restored to the candidate because this finding's
        implied deduction was voided (0 for a finding that stands - see
        AuditReport.score_adjustment, which is just the sum of these)."""
        return SEVERITY_WEIGHTS.get(self.severity, 0) if self.status != "SUPPORTED" else 0

    def to_dict(self) -> dict:
        return {
            "finding": self.claim,
            "severity": self.severity,
            "evidence_status": self.status,
            "evidence": self.evidence,
            "score_impact": self.score_impact,
        }


@dataclass
class AuditReport:
    findings: list[FindingAudit]

    def summary(self) -> dict:
        invalid_references = sum(
            1 for f in self.findings for c in f.evidence_checks if c.reason == "invalid_turn"
        )
        return {
            "total_findings": len(self.findings),
            "supported": sum(1 for f in self.findings if f.status == "SUPPORTED"),
            "not_established": sum(1 for f in self.findings if f.status == "NOT_ESTABLISHED"),
            "contradicted": sum(1 for f in self.findings if f.status == "CONTRADICTED"),
            "invalid_references": invalid_references,
        }

    def findings_detail(self) -> list[dict]:
        """The full explainable chain for every finding, in order -
        Finding -> Evidence -> Evidence status -> Score impact - meant to
        be persisted verbatim (see scoring_service.score_round4_submission's
        raw_llm_response_json["evidence_audit"]) so a rejected or accepted
        deduction can always be traced back to exactly what was cited and why."""
        return [f.to_dict() for f in self.findings]

    def surviving_claims(self) -> list[str]:
        """Claim text for every finding that actually cleared the audit -
        this, not the LLM's raw findings list, is what becomes Score.misses_json."""
        return [f.claim for f in self.findings if f.status == "SUPPORTED"]

    def score_adjustment(self) -> int:
        """Points to add back to the LLM's final_score - one rejected
        finding's implied deduction, reversed, per finding rejected. See
        scoring_service.score_round4_submission; this is what makes
        requirement 8 (an unsupported deduction must not survive) hold
        even though the LLM computes final_score as one holistic number
        rather than a literal running total."""
        return sum(f.score_impact for f in self.findings)


def _check_evidence(
    evidence: dict, flat: list[dict], turn_texts: list[str], turn_counts: dict[str, int],
    supporting_texts: list[str] | None = None, supporting_texts_by_tc: dict[str, list[str]] | None = None,
) -> EvidenceCheck:
    # A quote that appears verbatim in one of the caller's OTHER persisted
    # evidence artefacts (see audit_round4_findings' supporting_texts) is
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

    if is_quote_only and _normalize(quote_text):
        needle = _normalize(quote_text)
        # TC-scoped check, when the caller supplied per-test-case evidence
        # (supporting_texts_by_tc, keyed the same way
        # _auto_tc_audit_payload/round4_auto_scoring.txt label a test
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
            return EvidenceCheck(False, "wrong_test_case")

    # Unchanged from before this feature existed: a quote that appears
    # verbatim in one of the caller's OTHER persisted evidence artefacts
    # is established the same way a turn quote is - the text demonstrably
    # exists in something this submission actually recorded. Runs
    # regardless of is_quote_only (preserves the exact original condition
    # - any quote-bearing citation, not only quote-only ones) so every
    # existing caller's behavior is untouched byte-for-byte.
    if supporting_texts and quote_text and _normalize(quote_text):
        needle = _normalize(quote_text)
        if any(needle in text for text in supporting_texts):
            return EvidenceCheck(True, "ok_supporting_evidence")

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
    if not quote or not _normalize(quote):
        return EvidenceCheck(False, "missing_quote")
    if _normalize(quote) in turn_texts[turn_no - 1]:
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


def audit_round4_findings(
    test_cases: list[dict], findings: list[dict],
    supporting_texts: list[str] | dict[str, list[str]] | None = None,
) -> AuditReport:
    """The single entry point: check every LLM-generated finding against
    the transcript and return a verdict per finding, deterministically.
    `test_cases` is the same payload passed to llm_service.score_round4_conversation
    (list of {title, turns: [{turn_number, candidate_prompt, model_response}]});
    `findings` is the scorer's own findings list (list of dicts shaped
    like schemas.Round4Finding).

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
    turn_texts = [_turn_text(item["raw"]) for item in flat]
    turn_counts = {(tc.get("title") or ""): len(tc.get("turns") or []) for tc in test_cases or []}
    if isinstance(supporting_texts, dict):
        support_by_tc = {
            str(label): [_normalize(t) for t in (texts or []) if t and _normalize(t)]
            for label, texts in supporting_texts.items()
        }
        normalized_support = [text for texts in support_by_tc.values() for text in texts]
    else:
        support_by_tc = {}
        normalized_support = [_normalize(t) for t in (supporting_texts or []) if t and _normalize(t)]

    audited = []
    for raw in findings or []:
        claim = (raw.get("claim") or "").strip()
        severity = raw.get("severity") if raw.get("severity") in SEVERITY_WEIGHTS else "low"
        evidence = raw.get("evidence") or []

        if not evidence:
            status = "NOT_ESTABLISHED"
            checks: list[EvidenceCheck] = []
        else:
            checks = [_check_evidence(ev, flat, turn_texts, turn_counts, normalized_support, support_by_tc) for ev in evidence]
            if any(c.reason == "turns_exist" for c in checks):
                status = "CONTRADICTED"
            elif all(c.valid for c in checks):
                status = "SUPPORTED"
            else:
                status = "NOT_ESTABLISHED"

        if status != "CONTRADICTED" and _contradicted_by_followup(claim, evidence, flat):
            status = "CONTRADICTED"

        audited.append(FindingAudit(claim=claim, severity=severity, status=status, evidence=evidence, evidence_checks=checks))

    return AuditReport(findings=audited)
