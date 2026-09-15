"""
ROUND 5 -- PROGRESSIVE ENGINEERING (POC). Phase 3: the candidate-facing
Generator + structural stage isolation. See models_progressive.py
(Phase 1), progressive_service.py (Phase 2), and this session's Round 5
architecture-discovery notes for the full plan.

CRITICAL SECURITY RULE, enforced structurally, not by prompt wording:
the generator must never receive a future stage's requirement, hidden
tests, or reference solution. This is NOT achieved by loading everything
and instructing the model not to reveal it - see build_generator_context
below, the single function responsible for deciding what the generator
is allowed to know. It reuses progressive_service.get_current_requirement
(Phase 2), which already enforces "only ProgressiveRequirement WHERE
stage_order == attempt.current_stage, nothing else" - there is no other
code path in this module that ever queries ProgressiveRequirement, and
hidden_tests_json/reference_solution are never read by this module at
all, for ANY stage, current or otherwise. See test_progressive_llm.py's
isolation tests for the actual proof (inspecting the rendered prompt
text), not just this docstring's claim.

DOMAIN-NEUTRAL BY DESIGN: nothing here assumes CSV, a specific file
format, a specific language, or a specific function name - problem.
input_spec_json is rendered as opaque JSON, and the prompt itself
(progressive_generator.txt) never names a domain.

ISOLATION FROM THE REST OF THE APP: reuses llm_service's centralized
Claude client (_call_claude/_parse_json_response/_load_prompt) - the
same low-level infrastructure every round already shares - and
progressive_service.py (Phase 2, this round's own prior phase). Imports
nothing from Round 3 and is imported by nothing in Round 1-4.

NOT built in this phase: no Detector (Phase 1) or Judge (Phase 2)
wiring, no Policy Core, no routes, no turn persistence
(ProgressiveAiTurn rows) - generate_response is a pure call-and-return
function, callable in isolation for testing/replay only.
"""
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from . import llm_service
from . import progressive_service
from ..models_progressive import ProgressiveAttempt

_VALID_RESPONSE_KINDS = {"clarify", "refuse", "code_edit", "explain"}


@dataclass
class GeneratorContext:
    """Everything (and ONLY everything) the generator prompt is built
    from. Every field is either problem-level (the same regardless of
    stage - title/description/input_spec/language) or scoped to the
    CURRENT stage only. There is deliberately no field here for a future
    stage's requirement, hidden tests, or reference solution - not
    "present but unused," structurally absent from this type."""
    problem_title: str
    problem_description: str
    input_spec: dict
    language: str
    current_stage_requirement: str
    current_stage_expected_behavior: Optional[str]
    current_code: Optional[str]
    current_stage: int


@dataclass
class GeneratorResponse:
    """The generator's classified turn - same response_kind vocabulary
    as Round 3's hardened prompt (clarify/refuse/code_edit/explain), a
    proven, tested set of guardrails, reused as a pattern here (this
    module does not import Round 3's prompt or code)."""
    response_kind: str
    response_message: str
    code_after: Optional[str] = None


def build_generator_context(db: Session, attempt: ProgressiveAttempt) -> GeneratorContext:
    """The ONE function that decides what the generator is allowed to
    know for this turn. Raises (via progressive_service.get_current_requirement)
    if the attempt is archived or already completed - there is no
    "current stage" to build a context for in either case."""
    requirement = progressive_service.get_current_requirement(db, attempt)
    current_code = progressive_service.get_current_code(db, attempt)
    problem = attempt.problem
    return GeneratorContext(
        problem_title=problem.title,
        problem_description=problem.description,
        input_spec=problem.input_spec_json or {},
        language=problem.language,
        current_stage_requirement=requirement.requirement_text,
        current_stage_expected_behavior=requirement.expected_behavior,
        current_code=current_code,
        current_stage=attempt.current_stage,
    )


def render_prompt(context: GeneratorContext, candidate_request: str) -> str:
    """Split out from generate_response so tests can inspect the actual
    rendered prompt text without making a live LLM call - see
    test_progressive_llm.py's isolation tests, which call this directly."""
    return llm_service._load_prompt("progressive_generator.txt").format(
        problem_title=context.problem_title,
        problem_description=context.problem_description,
        input_spec=json.dumps(context.input_spec),
        language=context.language,
        current_stage_requirement=context.current_stage_requirement,
        current_stage_expected_behavior=context.current_stage_expected_behavior or "(not specified)",
        current_code=context.current_code or "(no code written yet)",
        candidate_request=candidate_request,
    )


def generate_response(db: Session, attempt: ProgressiveAttempt, candidate_request: str) -> GeneratorResponse:
    """The Generator - domain-neutral, structurally isolated from every
    stage beyond the current one (see build_generator_context and the
    module docstring). Not wired to Phase 1's detector or Phase 2's judge
    yet (that's Phase 4), and not called from any route yet."""
    context = build_generator_context(db, attempt)
    prompt = render_prompt(context, candidate_request)
    raw = llm_service._call_claude(prompt, max_tokens=4096)
    parsed = llm_service._parse_json_response(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Expected a JSON object for the generator's response, got: {type(parsed)}")
    response_kind = parsed.get("response_kind")
    if response_kind not in _VALID_RESPONSE_KINDS:
        raise ValueError(f"Generator returned an unrecognized response_kind: {response_kind!r}")
    return GeneratorResponse(
        response_kind=response_kind,
        response_message=parsed.get("response_message", ""),
        code_after=parsed.get("code_after"),
    )
