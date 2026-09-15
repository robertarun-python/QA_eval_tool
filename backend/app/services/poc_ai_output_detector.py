"""
Controlled AI Coding Assessment POC - Phase 1: a standalone, deterministic,
stateless detector that inspects one already-generated AI response and
classifies it SAFE / SUSPICIOUS / BLOCK before it would ever reach a
candidate. See the architecture discovery notes (this session) for the
full 5-phase plan this is Phase 1 of.

ISOLATION: this module is a new, separate POC component. It does not
import from, and must never be imported by, round3_construct_engine.py /
round3_constructs.py / llm_service.py / any Round 3 route or prompt.
Round 3's own (narrow, production) leak detector is untouched - this
generalizes the same PRINCIPLE (deterministic check wrapping an LLM's
self-reported output) in a new place, not a refactor of that one.

SCOPE DISCIPLINE (explicit product decision, not an oversight): this
module does NOT attempt to decide, on its own, whether a response
*semantically* hands over the solution or invents meaningful test data -
that requires understanding meaning, which is exactly why a separate
Judge LLM (Phase 2) is the next piece to build, not a bigger regex here.
Every check below is one of three kinds:
  (a) STRUCTURAL - the response's own claimed shape is internally
      inconsistent (e.g. "no code change" but the code changed anyway).
      Unambiguous by construction -> may BLOCK.
  (b) EXACT-MATCH LEAK - the caller explicitly supplies secret text
      (a reference solution, a hidden test literal, a future-stage
      requirement) and the response contains it verbatim. Literal
      containment, not inference -> may BLOCK.
  (c) SUSPICION SIGNAL - cheap, honestly-fallible heuristics (size,
      shape, literal-heavy content) that are good at "this needs a
      closer look" and bad at ever concluding "this is definitely a
      leak". These can only ever produce SUSPICIOUS, never BLOCK - the
      Judge, not a heuristic, gets to make that call.

Pure functions only: plain strings/lists in, a plain result out. No
database objects, no LLM calls, no network - fully unit-testable with
zero dependencies beyond the standard library.
"""
import difflib
import re
from dataclasses import dataclass, field

VERDICT_SAFE = "SAFE"
VERDICT_SUSPICIOUS = "SUSPICIOUS"
VERDICT_BLOCK = "BLOCK"

_SEVERITY_RANK = {VERDICT_SAFE: 0, VERDICT_SUSPICIOUS: 1, VERDICT_BLOCK: 2}

# Tunable, not tuned: a starting point for the "disproportionate change
# size" heuristic, not a claim these are the "right" numbers. Phase 2's
# judge is what actually decides hard cases - these constants only decide
# what's cheap enough to flag for a closer look.
_MIN_SUSPICIOUS_DIFF_LINES = 3
_DIFF_LINES_PER_INSTRUCTION_WORD = 1.0

_CODE_FENCE_RE = re.compile(r"```")
# A handful of unambiguous, multi-language code shape markers - deliberately
# NOT trying to be a real parser (see module docstring, this is a
# suspicion signal, not a verdict).
_CODE_LINE_RE = re.compile(
    r"^\s*(def |function |class |for |while |if |return\b|}|{|;\s*$|=\s*[^=])"
)
_NUMERIC_LIST_RE = re.compile(r"\[\s*-?\d+(?:\.\d+)?\s*(?:,\s*-?\d+(?:\.\d+)?\s*)+\]")


@dataclass
class DetectorFinding:
    """One check's result. Only ever produced when a check actually
    fires - a clean check contributes nothing to the result, not a
    passing finding, so callers/audits only ever see what's actionable."""
    check: str      # short machine-readable name, e.g. "contract_consistency"
    severity: str   # VERDICT_SUSPICIOUS or VERDICT_BLOCK - never VERDICT_SAFE
    detail: str     # human-readable, safe for an HR/audit view - never echoes a forbidden_snippets secret verbatim


@dataclass
class DetectorResult:
    verdict: str
    findings: list[DetectorFinding] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return self.verdict == VERDICT_SAFE


def _check_contract_consistency(claims_no_code: bool, previous_code: str | None, new_code: str | None) -> DetectorFinding | None:
    """(a) STRUCTURAL. A response that claims to be pure explanation/
    clarification/refusal must not actually change the code - if it did,
    the response's own claimed shape contradicts its own payload, which
    needs no semantic judgment to catch."""
    if not claims_no_code:
        return None
    if new_code is not None and new_code != previous_code:
        return DetectorFinding(
            check="contract_consistency",
            severity=VERDICT_BLOCK,
            detail="Response claims no code change, but the code payload differs from the prior version.",
        )
    return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_forbidden_snippets(
    response_text: str, new_code: str | None, forbidden_snippets: list[tuple[str, str]] | None,
) -> DetectorFinding | None:
    """(b) EXACT-MATCH LEAK. forbidden_snippets is a list of (label, secret
    text) pairs the CALLER supplies - a reference solution, a hidden test
    literal, a future-stage requirement, whatever must never appear
    verbatim. Whitespace/case-normalized substring containment only - no
    inference about meaning, so this can never have a false "it's
    probably fine" the way a semantic check could. The finding's detail
    names the label, never the secret itself, so the detector's own
    output is safe to log/show without re-leaking what it caught."""
    if not forbidden_snippets:
        return None
    haystack = _normalize(f"{response_text}\n{new_code or ''}")
    for label, secret in forbidden_snippets:
        secret_norm = _normalize(secret)
        if secret_norm and secret_norm in haystack:
            return DetectorFinding(
                check="forbidden_snippet_leak",
                severity=VERDICT_BLOCK,
                detail=f"Response contains a verbatim match of protected content labeled '{label}'.",
            )
    return None


def _check_disproportionate_change_size(
    candidate_instruction: str, previous_code: str | None, new_code: str | None, claims_no_code: bool,
) -> DetectorFinding | None:
    """(c) SUSPICION SIGNAL. "Detecting a large diff is easy" (explicit
    product direction) - this only measures size, never claims to know
    WHY the diff is large. A candidate legitimately pasting a big
    instruction that names many steps can produce a large, fully-earned
    diff; this check can't tell the difference, so it only ever
    escalates to SUSPICIOUS for the Judge (Phase 2) to actually decide."""
    if claims_no_code or new_code is None:
        return None
    before_lines = (previous_code or "").splitlines()
    after_lines = new_code.splitlines()
    diff_lines = sum(
        1 for line in difflib.ndiff(before_lines, after_lines)
        if line.startswith("+ ") or line.startswith("- ")
    )
    instruction_words = max(1, len(candidate_instruction.split()))
    allowance = _MIN_SUSPICIOUS_DIFF_LINES + instruction_words * _DIFF_LINES_PER_INSTRUCTION_WORD
    if diff_lines > allowance:
        return DetectorFinding(
            check="disproportionate_change_size",
            severity=VERDICT_SUSPICIOUS,
            detail=(
                f"Code changed by {diff_lines} line(s) for an instruction of {instruction_words} word(s) - "
                f"larger than expected for a narrow, single-instruction edit. Not conclusive on its own."
            ),
        )
    return None


def _check_code_shaped_content_in_no_code_response(claims_no_code: bool, response_text: str) -> DetectorFinding | None:
    """(c) SUSPICION SIGNAL. A response claiming to be pure explanation/
    clarification should be prose, not code. Fenced code blocks are a
    strong signal; a run of code-shaped lines is a weaker one - neither
    proves the content is actually a working solution (an explanation
    can legitimately quote one short line, e.g. "the line `x = 5`"), so
    this stays SUSPICIOUS, never BLOCK."""
    if not claims_no_code:
        return None
    if _CODE_FENCE_RE.search(response_text):
        return DetectorFinding(
            check="code_shaped_content_in_explanation",
            severity=VERDICT_SUSPICIOUS,
            detail="Response claims no code change but contains a fenced code block.",
        )
    code_like_lines = sum(1 for line in response_text.splitlines() if _CODE_LINE_RE.match(line))
    if code_like_lines >= 2:
        return DetectorFinding(
            check="code_shaped_content_in_explanation",
            severity=VERDICT_SUSPICIOUS,
            detail=f"Response claims no code change but contains {code_like_lines} code-shaped lines.",
        )
    return None


def _check_literal_data_list_in_no_code_response(claims_no_code: bool, response_text: str) -> DetectorFinding | None:
    """(c) SUSPICION SIGNAL. The exact shape of the original reported gap:
    an assistant inventing concrete sample/test values (e.g. "[3, 5, 9,
    1]") inside a prose response instead of asking the candidate to
    supply them. A bracketed numeric list is a cheap, honest proxy for
    "literal data appeared here" - it cannot tell whether those values
    were actually invented by the assistant or quoted back from
    something the candidate already gave it, so this is SUSPICIOUS only."""
    if not claims_no_code:
        return None
    if _NUMERIC_LIST_RE.search(response_text):
        return DetectorFinding(
            check="literal_data_in_explanation",
            severity=VERDICT_SUSPICIOUS,
            detail="Response includes what looks like literal sample/test data values - verify the candidate supplied these themselves.",
        )
    return None


def inspect_response(
    candidate_instruction: str,
    claims_no_code: bool,
    response_text: str,
    previous_code: str | None = None,
    new_code: str | None = None,
    forbidden_snippets: list[tuple[str, str]] | None = None,
) -> DetectorResult:
    """Run every deterministic check against one already-generated AI
    response and return the aggregate verdict - the highest severity
    among whatever findings fired, or SAFE if none did.

    Args:
        candidate_instruction: the candidate's own request text this turn.
        claims_no_code: True if the response claims to be pure
            explanation/clarification/refusal (no code change intended).
        response_text: the natural-language message part of the response.
        previous_code: the code buffer before this response, if any.
        new_code: the code payload this response would show, if any.
        forbidden_snippets: (label, secret_text) pairs that must never
            appear verbatim in the response - e.g. a reference solution,
            a hidden test literal, a future-stage requirement.
    """
    findings = []
    for check in (
        lambda: _check_contract_consistency(claims_no_code, previous_code, new_code),
        lambda: _check_forbidden_snippets(response_text, new_code, forbidden_snippets),
        lambda: _check_disproportionate_change_size(candidate_instruction, previous_code, new_code, claims_no_code),
        lambda: _check_code_shaped_content_in_no_code_response(claims_no_code, response_text),
        lambda: _check_literal_data_list_in_no_code_response(claims_no_code, response_text),
    ):
        finding = check()
        if finding is not None:
            findings.append(finding)

    verdict = VERDICT_SAFE
    for finding in findings:
        if _SEVERITY_RANK[finding.severity] > _SEVERITY_RANK[verdict]:
            verdict = finding.severity

    return DetectorResult(verdict=verdict, findings=findings)
