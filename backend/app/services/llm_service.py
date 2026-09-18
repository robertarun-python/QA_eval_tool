"""
Every call to the Claude API goes through this file. Two reasons to
centralize it rather than calling `anthropic.Anthropic()` from inside
routers: (1) one place to change models/retry logic later, (2) prompts
live in text files under app/prompts/, loaded here, so the actual
wording is easy to find and edit without touching Python.
"""
import hashlib
import json
from pathlib import Path

import anthropic
from pydantic import ValidationError

from ..config import settings
from ..schemas import Round4TurnResponse, Round4EnvironmentOut, Round4UiMockupOut, Round3CodingTurnResponse, Round4Finding, Round4PilotTurnResponse
from . import round3_constructs
from . import round3_construct_engine
from . import round3_policy
from . import round4_pilot_policy
from . import round4_auto_policy

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Copy .env.example to .env "
                "and fill in a real key from console.anthropic.com."
            )
        # Explicit timeout, not the SDK's default (several minutes) - two
        # call sites run synchronously in the request path (round4_turn,
        # which a candidate is actively waiting on mid-assessment, and
        # HR's _generate_reference) and both now fail cleanly on an
        # ERROR (see the try/except wrapping at each call site), but
        # without this a slow response just hangs the request with no
        # feedback for however long the default allows. 30.0 measured as
        # too tight in practice - a real round2_reference_generation.txt
        # call timed (via direct reproduction) at ~37s on a normal day,
        # well past the old limit, causing "Reference generation failed"
        # on a perfectly healthy request. 60.0 gives real calls headroom
        # without letting a truly hung request block a candidate/HR
        # indefinitely.
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=60.0)
    return _client


def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _prompt_hash(prompt_text: str) -> str:
    """Short, content-based version tag for a prompt file - not mtime
    (doesn't survive a copy/redeploy) and not a fixed version number
    (prompts are hand-edited .txt files with no versioning workflow of
    their own, see ARCHITECTURE.md). Truncated to 12 hex chars: this is
    for a human glancing at "which prompt scored this", not cryptographic
    collision resistance."""
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:12]


def _scoring_provenance(prompt_file: str, prompt_text: str) -> dict:
    return {"model": settings.claude_model, "prompt_file": prompt_file, "prompt_hash": _prompt_hash(prompt_text)}


def _call_claude(prompt: str, max_tokens: int = 4096) -> str:
    client = _get_client()
    message = client.messages.create(
        model=settings.claude_model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def _parse_json_response(raw_text: str) -> dict | list:
    """
    Claude is instructed to return only JSON, but models occasionally
    wrap it in ```json fences or add a stray sentence. Strip common
    wrapping before parsing rather than trusting raw output blindly.

    strict=False permits a raw control character - in practice a literal
    newline - inside a string value. Observed repeatedly when a response
    carries a whole source file: the model writes the opening `\"\"\"` of a
    docstring and then a real newline instead of `\\n`, and strict parsing
    rejects the entire response ("Invalid control character at ..."), which
    surfaced as a hard 502 and made the automation round unusable. This is
    the mirror image of the over-escaping case _repair_escaped_code
    handles. It only ever WIDENS what parses - every response that decodes
    today decodes identically - so it cannot break an existing caller.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip(), strict=False)


# ---- Round 1 ----

def generate_round1_reference(scenario_description: str, experience_band: str, time_limit_minutes: int) -> list[dict]:
    prompt = _load_prompt("round1_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
        time_limit_minutes=time_limit_minutes,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, list):
        raise ValueError(f"Expected a JSON array of test cases, got: {type(result)}")
    return result


def score_round1_submission(
    scenario_description: str,
    experience_band: str,
    reference_cases: list[dict],
    candidate_submission: str,
) -> dict:
    prompt_text = _load_prompt("round1_scoring.txt")
    prompt = prompt_text.format(
        scenario_description=scenario_description,
        experience_band=experience_band,
        reference_cases=json.dumps(reference_cases, indent=2),
        candidate_submission=candidate_submission,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    result["_provenance"] = _scoring_provenance("round1_scoring.txt", prompt_text)
    return result


# ---- Round 2 ----
#
# Same two-call shape as round 1 (generate a reference, then score
# against it), but candidate submissions are NOT the same row shape as
# the reference here. The reference is still ordered debugging steps
# (title, preconditions, steps, expected_result - reused for HR's
# review UI, see openScenarioDetail hiding priority/type for round 2).
# What the candidate actually submits is deliberately simpler and
# investigation-shaped: a list of areas they checked, plus one
# concluding root-cause statement - see schemas.Round2SubmissionCreate
# and renderInvestigationForm in app.js.

def generate_round2_reference(scenario_description: str, experience_band: str, time_limit_minutes: int) -> list[dict]:
    prompt = _load_prompt("round2_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
        time_limit_minutes=time_limit_minutes,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, list):
        raise ValueError(f"Expected a JSON array of debugging steps, got: {type(result)}")
    return result


def score_round2_submission(
    scenario_description: str,
    experience_band: str,
    reference_steps: list[dict],
    candidate_investigation: list[dict],
    candidate_root_cause: str,
) -> dict:
    prompt_text = _load_prompt("round2_debug_scoring.txt")
    prompt = prompt_text.format(
        scenario_description=scenario_description,
        experience_band=experience_band,
        reference_steps=json.dumps(reference_steps, indent=2),
        candidate_investigation=json.dumps(candidate_investigation, indent=2),
        candidate_root_cause=candidate_root_cause,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    result["_provenance"] = _scoring_provenance("round2_debug_scoring.txt", prompt_text)
    return result


# ---- Round 3 (AI-prompted coding: the candidate never writes code
# directly - they direct the LLM turn by turn, and it writes/edits the
# actual source. See
# docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md.) ----

def generate_round3_reference(scenario_description: str, experience_band: str) -> dict:
    prompt = _load_prompt("round3_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if (
        not isinstance(result, dict)
        or "test_cases" not in result
        or "expected_approach" not in result
        or not isinstance(result.get("reference_solution"), str)
        or not result["reference_solution"].strip()
    ):
        raise ValueError(
            f"Expected a JSON object with 'test_cases', 'expected_approach', and a non-empty "
            f"'reference_solution' string, got: {result!r}"
        )
    unknown = set(result.get("required_constructs", [])) - set(round3_constructs.CONSTRUCT_CATEGORIES)
    if unknown:
        raise ValueError(f"required_constructs contains unknown categories: {sorted(unknown)}")
    return result


def round3_coding_turn(
    scenario_description: str,
    language: str,
    conversation_so_far: list[dict],
    current_code: str | None,
    candidate_prompt: str,
    turn_number: int,
    required_constructs: list[str] | None = None,
    declared_constructs: dict | None = None,
) -> dict:
    required_constructs = required_constructs or []
    declared_constructs = declared_constructs or {}
    open_categories = [c for c in required_constructs if c not in declared_constructs]

    if round3_policy.is_prohibited(candidate_prompt):
        # Deterministic pre-generation refusal - _raw_turn (the only path
        # to _call_claude in this function) is never invoked. See
        # round3_policy.py's module docstring for why this exists.
        return {
            "response_kind": "refuse",
            "response_message": round3_policy.REFUSAL_MESSAGE,
            "code_after": None,
            "declared_constructs": dict(declared_constructs),
        }

    def _raw_turn(regeneration_note: str = "") -> Round3CodingTurnResponse:
        prompt = _load_prompt("round3_coding_turn.txt").format(
            scenario_description=scenario_description,
            language=language,
            conversation_so_far=json.dumps(conversation_so_far, indent=2),
            current_code=current_code or "(no code written yet)",
            candidate_prompt=candidate_prompt,
            turn_number=turn_number,
            is_first_turn="true" if turn_number == 1 else "false",
            open_categories=json.dumps(open_categories),
            declared_constructs=json.dumps(declared_constructs, indent=2),
            regeneration_note=regeneration_note,
        )
        # 4096, not the 2048 used before this feature - the response now
        # carries a full code snapshot AND a category_status block (one
        # neutral_question per open category) in the same JSON object.
        raw = _call_claude(prompt, max_tokens=4096)
        result = _parse_json_response(raw)
        if not isinstance(result, dict):
            raise ValueError(f"Expected a JSON object for the assistant's turn, got: {type(result)}")
        try:
            return Round3CodingTurnResponse.model_validate(result)
        except ValidationError as e:
            raise ValueError(f"Assistant's turn response didn't match the expected shape: {e}") from e

    parsed = _raw_turn()

    # "explain" never touches code or declares anything either, same as
    # "refuse" - both skip the construct-checklist engine below.
    if parsed.response_kind in ("refuse", "explain") or not open_categories:
        return {
            "response_kind": parsed.response_kind,
            "response_message": parsed.response_message,
            "code_after": parsed.code_after,
            # A fresh copy, not the caller's own dict by reference - the
            # other two return paths below both hand back the engine's
            # own fresh dict, and two Round3Turn rows must never end up
            # aliasing the same mutable object.
            "declared_constructs": dict(declared_constructs),
        }

    category_status = {k: v.model_dump() for k, v in parsed.category_status.items()}
    decision = round3_construct_engine.decide(category_status, declared_constructs, required_constructs)

    if decision.final_kind == "proceed":
        # Nothing this instruction attempted was left construct-vague,
        # but that only means the checklist isn't the blocker - other
        # required categories may still be open, staying silent until a
        # later instruction addresses them. The model's OWN
        # classification for this turn (already schema-valid: code_after
        # is present iff response_kind == "code_edit") still governs
        # whether this is actually a code_edit or a non-construct
        # clarify (e.g. still missing an ordinary name/technique).
        # "refuse" can't reach here - it already short-circuited above.
        return {
            "response_kind": parsed.response_kind,
            "response_message": parsed.response_message,
            "code_after": parsed.code_after,
            "declared_constructs": decision.updated_state,
        }

    # clarify: check for vocabulary leaks, regenerate once, then fall back.
    # A category the model didn't give a usable neutral_question for -
    # missing from category_status entirely, or present with
    # neutral_question absent/None (both schema-legal shapes a live model
    # can produce) - goes straight to the fallback template rather than
    # being indexed and crashing.
    def _has_question(status_map: dict, category: str) -> bool:
        entry = status_map.get(category)
        return bool(entry and entry.get("neutral_question"))

    unusable = {c for c in decision.ask_categories if not _has_question(category_status, c)}
    checkable = [c for c in decision.ask_categories if c not in unusable]

    leaked = round3_construct_engine.leaking_categories(checkable, category_status, language)
    if leaked:
        # Never name the leaking category here - the category's own key
        # (e.g. "iteration") is itself forbidden vocabulary for that
        # category, so echoing it back would hand the model exactly the
        # word it must avoid, one line above asking it not to.
        note = (
            "Your last attempt named the very thing it was trying to test for in one "
            "or more of the questions you drafted for the checklist above. Redraft "
            "EVERY neutral_question you produce this time so it describes only the "
            "underlying need - never the concept, technique, or vocabulary itself, "
            "however that concept is normally referred to in code or in plain English."
        )
        retry = _raw_turn(regeneration_note=note)
        retry_status = {k: v.model_dump() for k, v in retry.category_status.items()}
        for category in leaked:
            # Merge only the rephrased question, never the retry's status/
            # value - a retry that reclassifies the same instruction as
            # "declared" would otherwise install a None neutral_question
            # and crash the re-check below, and would also bypass the
            # cumulative-state merge decide() already computed this turn.
            if _has_question(retry_status, category):
                category_status[category]["neutral_question"] = retry_status[category]["neutral_question"]
            else:
                unusable.add(category)
        leaked = round3_construct_engine.leaking_categories(
            [c for c in leaked if c not in unusable], category_status, language
        )

    use_fallback_for = set(leaked) | unusable
    message = round3_construct_engine.assemble_message(decision.ask_categories, category_status, use_fallback_for)
    return {
        "response_kind": "clarify",
        "response_message": message,
        "code_after": None,
        "declared_constructs": decision.updated_state,
    }


def round3_syntax_fix(
    code: str,
    language: str,
    required_constructs: list[str] | None = None,
    declared_constructs: dict | None = None,
) -> dict:
    required_constructs = required_constructs or []
    declared_constructs = declared_constructs or {}

    prompt = _load_prompt("round3_syntax_fix.txt").format(
        code=code,
        language=language,
        required_constructs=json.dumps(required_constructs),
    )
    raw = _call_claude(prompt, max_tokens=4096)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the syntax-fix response, got: {type(result)}")
    try:
        parsed = Round3CodingTurnResponse.model_validate(result)
    except ValidationError as e:
        raise ValueError(f"Syntax-fix response didn't match the expected shape: {e}") from e

    if parsed.response_kind != "direct_edit":
        # Model drift, not candidate error: the prompt only ever
        # describes "direct_edit", but the shared schema still legally
        # accepts "clarify"/"refuse"/"code_edit" too. Those shapes are
        # allowed to have code_after=None and a response_message that is
        # literally a candidate-facing question - neither is acceptable
        # on this path (spec §4: "no gap-flagging... nothing is shown to
        # the candidate"). Fail safe: the candidate's own code is never
        # lost, and nothing resembling a question ever reaches them.
        return {
            "response_message": "I couldn't process that as a syntax fix - your code was saved unchanged.",
            "code_after": code,
            "declared_constructs": dict(declared_constructs),
        }

    category_status = {k: v.model_dump() for k, v in parsed.category_status.items()}
    updated_state = round3_construct_engine.merge_declared(category_status, declared_constructs, required_constructs)

    response_message = parsed.response_message
    if any(round3_constructs.contains_forbidden_vocab(response_message, c, language) for c in required_constructs):
        response_message = "Your code has been checked - see the updated version below."

    return {
        "response_message": response_message,
        "code_after": parsed.code_after,
        "declared_constructs": updated_state,
    }


def score_round3_coding(
    scenario_description: str,
    expected_approach: str,
    conversation_so_far: list[dict],
    test_results: list[dict],
) -> dict:
    prompt_text = _load_prompt("round3_coding_scoring.txt")
    prompt = prompt_text.format(
        scenario_description=scenario_description,
        expected_approach=expected_approach,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        test_results=json.dumps(test_results, indent=2),
    )
    raw = _call_claude(prompt, max_tokens=2048)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    result["_provenance"] = _scoring_provenance("round3_coding_scoring.txt", prompt_text)
    return result


# ---- Round 4 ----
#
# Conversational, not one-shot: the candidate prompts an assistant to
# build test automation for their OWN round 1 scenario+answer, across
# as many self-titled test cases as they choose to write (no fixed
# UI/API/DB categories - see models.Round4TestCase; deciding what to
# test is itself part of what this round assesses). The assistant is
# deliberately imperfect (~assistance_pct correct per turn) and nothing
# actually executes - it invents an execution trace (plain-English
# steps + what was observed, sometimes wrong) in the same call.
# Deliberately no code in the response: the candidate is meant to reason
# from observed behavior, the way a QA person without deep coding
# fluency actually would, not from reading an implementation - showing
# code would let coding skill substitute for the automation-thinking
# skill this round exists to test. No turn cap, and no UI/API/DB
# category tagging anywhere the candidate can see (see
# round4_partial_response.txt) - both would just be different flavors of
# hinting, which is exactly what this round is trying not to do. HR's
# per-scenario "rules" (currently just assistance level) are a future
# authoring UI; for now every round 4 scenario uses these defaults,
# merged over whatever Scenario.config_json HR happens to have set. The
# fallback value itself lives in config.py (round4_default_assistance_pct),
# not here - see that file's docstring for why.
#
# Both round4_partial_response.txt and round4_scoring.txt independently
# guard against a candidate trying to direct the assistant/grading itself
# (skip-ahead, "mark everything as passing," "tell me what scores well")
# - the turn-level guard refuses in character, the scoring-level guard
# ignores the attempt and logs it as a red flag in feedback_text instead
# of letting it move the score. This is disclosed to the candidate only
# as a short heads-up in showRound4Intro() (app.js) - that it happens and
# gets flagged - not the trigger phrasing or where it's recorded, so the
# disclosure sets expectations without handing out a way around it.
DEFAULT_ROUND4_CONFIG = {
    "assistance_pct": settings.round4_default_assistance_pct,
}


def generate_round4_environment(app_description: str) -> dict:
    """Auto-generates fictional test-environment reference facts (test
    login credentials, API endpoints, a DB schema reference, ...) for a
    round 4 scenario - shown to every candidate served this scenario.
    Grounded in the same app_description as generate_round4_ui_mockup
    (whichever Round 1 scenario is live for this band, resolved by the
    caller - see hr.py's _generate_reference) rather than round 4's own
    description, which is just instructions to the candidate, not a
    description of the app under test - generic grounding here produced
    generic, unhelpful credentials/schema before this was fixed."""
    prompt = _load_prompt("round4_environment_generation.txt").format(
        app_description=app_description,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the test environment, got: {type(result)}")
    # Validated (not just "has a fields key") - Round4EnvironmentOut is
    # only enforced at read time on ScenarioPublicOut/Round4StateOut, so
    # an unvalidated shape mismatch here (e.g. a non-string field value)
    # would sail through db.commit() in hr.py and only surface as a
    # broken candidate-facing read later. HR's own ScenarioOut uses a
    # loose dict, so this doesn't affect HR's own preview either way -
    # it's purely about not shipping a bad shape live.
    try:
        return Round4EnvironmentOut.model_validate(result).model_dump()
    except ValidationError as e:
        raise ValueError(f"Test environment response didn't match the expected shape: {e}") from e


def generate_round4_ui_mockup(app_description: str) -> dict:
    """Auto-generates a structured (never raw HTML) reference sketch of
    the app's screens - shown to the candidate as a static visual
    reference, the way a real QA automation engineer would have the
    actual app open in a browser. Grounded in whichever Round 1 scenario
    is live for this band (the actual "app" a candidate's round 1
    answer/round 4 automation is about) - see hr.py's _generate_reference
    for that lookup; app_description is already resolved by the caller,
    this function doesn't know or care where it came from."""
    prompt = _load_prompt("round4_ui_mockup_generation.txt").format(
        app_description=app_description,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the UI mockup, got: {type(result)}")
    try:
        return Round4UiMockupOut.model_validate(result).model_dump()
    except ValidationError as e:
        raise ValueError(f"UI mockup response didn't match the expected shape: {e}") from e


def _round4_needs_forced_flaw(conversation_so_far: list[dict], response: dict) -> bool:
    """True exactly when no turn in this test case - every prior one, AND
    the response that was just generated for this one - has reported
    anything other than status=='pass'. round4_partial_response.txt
    already asks the model to force a flaw onto the first turn with a
    genuine checkable outcome on its own; this is the deterministic
    backstop for when it doesn't comply (verified in practice to happen
    often enough that the prompt instruction alone isn't a real
    guarantee - see round4_force_flaw.txt for the follow-up call this
    gates). Once any turn has come back non-"pass", the requirement is
    already satisfied and this returns False for the rest of the test
    case, same as the prompt-level rule."""
    prior_all_pass = all(t["model_response"].get("status") == "pass" for t in conversation_so_far)
    return prior_all_pass and response.get("status") == "pass"


def _round4_force_flaw(environment: dict | None, candidate_prompt: str, response: dict) -> dict:
    prompt = _load_prompt("round4_force_flaw.txt").format(
        environment_json=json.dumps(environment, indent=2) if environment else "(none provided)",
        candidate_prompt=candidate_prompt,
        original_response_json=json.dumps(response, indent=2),
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the forced-flaw revision, got: {type(result)}")
    return Round4TurnResponse.model_validate(result).model_dump()


def round4_respond(
    test_case_title: str,
    environment: dict | None,
    scenario_instructions: str,
    round1_context: dict,
    conversation_so_far: list[dict],
    candidate_prompt: str,
    assistance_pct: int,
    turn_number: int,
) -> dict:
    prompt = _load_prompt("round4_partial_response.txt").format(
        test_case_title=test_case_title or "(untitled test case)",
        environment_json=json.dumps(environment, indent=2) if environment else "(none provided)",
        scenario_instructions=scenario_instructions,
        round1_scenario_title=round1_context["scenario_title"],
        round1_scenario_description=round1_context["scenario_description"],
        round1_submitted_rows=json.dumps(round1_context["submitted_rows"], indent=2),
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        candidate_prompt=candidate_prompt,
        assistance_pct=assistance_pct,
        turn_number=turn_number,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the assistant's turn, got: {type(result)}")
    # Validated against Round4TurnResponse's exact shape (status must be
    # literally "pass"/"fail"/"partial", steps/observed_result required)
    # before this ever reaches the caller - candidate.py's round4_turn
    # persists whatever this returns immediately, and every later read of
    # that candidate's round 4 state is response_model=Round4StateOut,
    # which validates just as strictly. An unvalidated shape mismatch
    # here used to sail straight into the database, permanently 500ing
    # every future read of that candidate's transcript - including the
    # very next one the frontend makes after sending this message - with
    # no way for the candidate to recover mid-assessment. Raising here
    # instead makes a bad turn a retryable failure, the same as malformed
    # JSON already is, rather than a silent, permanent one.
    try:
        validated = Round4TurnResponse.model_validate(result).model_dump()
    except ValidationError as e:
        raise ValueError(f"Assistant's turn response didn't match the expected shape: {e}") from e

    if _round4_needs_forced_flaw(conversation_so_far, validated):
        # Best-effort: if this second call itself fails for ANY reason -
        # a malformed/unvalidated response, or an API-level failure from
        # _call_claude itself (a timeout, rate limit, connection error -
        # none of which are ValueError/ValidationError) - the candidate
        # still gets the original (clean) turn rather than a 502 on an
        # otherwise-successful response. A missed early flaw is a worse
        # experience to recover from than swallowing this is.
        try:
            return _round4_force_flaw(environment, candidate_prompt, validated)
        except Exception:
            pass
    return validated


# Trial feature (see routers/candidate.py's GET /round/4/turn/{id}/code): an
# on-demand, candidate-facing rendering of an already-completed turn as a
# code snippet, in a language the candidate picks. Deliberately generated
# from the turn's OWN already-recorded steps/observed_result rather than
# fresh - this can only re-describe what's already visible in the trace,
# never decide or reveal anything new. The one rule that matters more than
# realism: round4_code_snippet.txt forbids any assertion or pass/fail logic
# in the generated code, since an assertion would tell the candidate
# whether something is correct before they've verified it themselves - the
# entire judgment this round exists to test. Isolated on purpose (its own
# prompt file, no persistence, no effect on scoring) so it's easy to remove
# if it turns out to still leak too much in practice.
def generate_round4_code_snippet(test_case_title: str, steps: list[dict], observed_result: str, language: str) -> str:
    prompt = _load_prompt("round4_code_snippet.txt").format(
        test_case_title=test_case_title or "(untitled test case)",
        steps_json=json.dumps(steps, indent=2),
        observed_result=observed_result,
        language=language,
    )
    raw = _call_claude(prompt, max_tokens=1024)
    text = raw.strip()
    if text.startswith("```"):
        # Strip an accidental code fence and optional language tag on the
        # first line, despite being told not to use one - models sometimes
        # add it out of habit.
        text = text[3:]
        first_newline = text.find("\n")
        if first_newline != -1 and len(text[:first_newline].split()) <= 1:
            text = text[first_newline + 1:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


# The only fields either evidence bucket is allowed to carry - see
# score_round4_conversation's docstring and ARCHITECTURE.md's Round 4
# section. Enforced with an assertion, not just a docstring, so a future
# edit that starts stuffing round2/round3/another-candidate's data into
# either dict fails loudly here instead of silently reaching the prompt.
_ROUND4_EVIDENCE_KEYS = {"test_cases"}
_ROUND1_REFERENCE_KEYS = {"scenario_title", "scenario_description", "submitted_rows"}


def score_round4_conversation(round4_evidence: dict, round1_reference_context: dict, assistance_pct: int) -> dict:
    """Scores ONE Round 4 session. The two dicts below are an explicit,
    enforced evidence hierarchy - see prompts/round4_scoring.txt's own
    PRIMARY EVIDENCE / REFERENCE ONLY labels, which this function's
    prompt-building must keep in lockstep with:

    - round4_evidence: PRIMARY EVIDENCE. The current Round 4 submission's
      own test cases and transcript ONLY - the sole source of truth for
      what this candidate actually did in this round. Built fresh by
      scoring_service.score_round4_submission from THIS submission's own
      round4_test_cases/turns; never from another round, another
      candidate, or a cached/previous evaluation.
    - round1_reference_context: REFERENCE ONLY. Just enough of the
      candidate's own Round 1 work (their scenario + their own test
      case rows) to understand what app/scenario Round 4 is automating.
      Never a source of Round 4 findings - see the prompt's own
      guardrail language.

    Round 2, Round 3, other candidates' data, and any prior scoring
    result are FORBIDDEN here by construction: neither parameter has a
    field for any of them (see _ROUND4_EVIDENCE_KEYS/_ROUND1_REFERENCE_KEYS
    below), so there's no slot for a caller to accidentally put one in."""
    assert set(round4_evidence) <= _ROUND4_EVIDENCE_KEYS, f"round4_evidence carries unexpected keys: {set(round4_evidence) - _ROUND4_EVIDENCE_KEYS}"
    assert set(round1_reference_context) <= _ROUND1_REFERENCE_KEYS, f"round1_reference_context carries unexpected keys: {set(round1_reference_context) - _ROUND1_REFERENCE_KEYS}"

    prompt_text = _load_prompt("round4_scoring.txt")
    prompt = prompt_text.format(
        round1_scenario_title=round1_reference_context["scenario_title"],
        round1_scenario_description=round1_reference_context["scenario_description"],
        round1_submitted_rows=json.dumps(round1_reference_context["submitted_rows"], indent=2),
        assistance_pct=assistance_pct,
        test_cases_json=json.dumps(round4_evidence["test_cases"], indent=2),
    )
    raw = _call_claude(prompt, max_tokens=8192)  # covers every test case's transcript + feedback
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    if "findings" not in result:
        # The prompt above ALWAYS asks for "findings" (never the old flat
        # "misses" list) - a real response missing it is a malformed LLM
        # response, not a legacy shape to fall back to silently. Treating
        # it as the latter would let a deduction bypass
        # round4_evidence_audit entirely (scoring_service._round4_findings_to_misses
        # only audits when "findings" is present) - exactly the
        # unaudited-score gap this module exists to close. Raising here
        # routes it through the same scoring_failed/retry path every
        # other malformed scoring response already takes (see
        # scoring_service.score_submission_in_background).
        #
        # Tests that intentionally exercise the old flat-misses shape
        # (e.g. test_round4.py's FAKE_SCORE) monkeypatch
        # score_round4_conversation itself, so this check - inside the
        # real function body - never runs for them.
        raise ValueError(f"Expected a 'findings' key in the round 4 scoring response, got keys: {list(result)}")
    # Validated here (schema shape only - not against the transcript,
    # that's round4_evidence_audit's job) so a single malformed finding
    # degrades to "unsupported, drop it" rather than crashing the whole
    # scoring call - same reasoning as _safe_fallback in poc_ai_judge.py.
    # A finding missing entirely wouldn't have cost the candidate
    # anything either, so this is the safe direction.
    validated = []
    for entry in result["findings"] or []:
        try:
            validated.append(Round4Finding.model_validate(entry).model_dump())
        except ValidationError:
            continue
    result["findings"] = validated
    result["_provenance"] = _scoring_provenance("round4_scoring.txt", prompt_text)
    return result


# ---- Round 4 pilot ("Focused Automation Pilot") ----
#
# A different shape from the legacy round 4 above (candidate writes and
# edits a real single-file Python automation, not a plain-English
# conversation with a role-playing AI) - see round4_pilot_policy.py and
# scoring_service.score_round4_pilot_submission. Deliberately separate
# functions from round4_respond/score_round4_conversation above, not a
# branch inside them, so the legacy round 4 path is untouched by this.

def round4_pilot_turn(
    scenario_instructions: str,
    current_code: str,
    conversation_so_far: list[dict],
    candidate_prompt: str,
) -> dict:
    """One AI turn in the Round 4 pilot. Deterministic policy check FIRST
    - round4_pilot_policy.is_prohibited - so a prohibited request never
    reaches _call_claude at all, same discipline as round3_coding_turn's
    own pre-generation check."""
    if round4_pilot_policy.is_prohibited(candidate_prompt):
        return {"response_kind": "refuse", "response_message": round4_pilot_policy.REFUSAL_MESSAGE, "code_after": None}

    prompt = _load_prompt("round4_pilot_turn.txt").format(
        current_code=current_code,
        scenario_instructions=scenario_instructions,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        candidate_prompt=candidate_prompt,
    )
    raw = _call_claude(prompt, max_tokens=4096)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the pilot turn, got: {type(result)}")
    return Round4PilotTurnResponse.model_validate(result).model_dump()


def score_round4_pilot_conversation(
    scenario_instructions: str, starter_code: str, final_code: str,
    turns: list[dict], clarification: dict | None, execution_result: dict,
    reference_solution: str, validation_notes: str,
) -> dict:
    """Scores one Round 4 pilot submission against the 5-area rubric (25/
    20/15/20/20 = 100 - see prompts/round4_pilot_scoring.txt). Findings go
    through the SAME round4_evidence_audit.audit_round4_findings backstop
    as the legacy round 4 path (see scoring_service.score_round4_pilot_submission) -
    reference_solution/validation_notes are REFERENCE ONLY, exactly like
    round1_reference_context is for score_round4_conversation above; never
    a source of findings about what the candidate did."""
    prompt_text = _load_prompt("round4_pilot_scoring.txt")
    prompt = prompt_text.format(
        scenario_instructions=scenario_instructions,
        starter_code=starter_code,
        final_code=final_code,
        turns_json=json.dumps(turns, indent=2),
        clarification_json=json.dumps(clarification, indent=2) if clarification else "(candidate did not ask for clarification)",
        execution_result_json=json.dumps(execution_result, indent=2),
        reference_solution=reference_solution,
        validation_notes=validation_notes,
    )
    raw = _call_claude(prompt, max_tokens=8192)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    if "findings" not in result:
        raise ValueError(f"Expected a 'findings' key in the round 4 pilot scoring response, got keys: {list(result)}")
    validated = []
    for entry in result["findings"] or []:
        try:
            validated.append(Round4Finding.model_validate(entry).model_dump())
        except ValidationError:
            continue
    result["findings"] = validated
    result["_provenance"] = _scoring_provenance("round4_pilot_scoring.txt", prompt_text)
    return result


# ---- AI-Assisted Test Automation round ----
#
# The candidate automates test cases THEY designed in round 1. Separate
# functions from both the legacy round 4 and the pilot above, not a branch
# inside them, so neither existing flow is touched by this one.

def _repair_escaped_code(code: str | None) -> str | None:
    """Undoes a double-escaped code block.

    Observed live: the model sometimes writes its JSON string with the
    escape sequences already escaped, so `\\n` arrives as a literal
    backslash + "n" instead of a newline. json.loads faithfully returns
    that, and the result is a single-line file that fails to execute with
    a SyntaxError on line 1 - the candidate's generated automation is
    simply unusable.

    The guard compares how many escaped pairs the block contains against
    how many REAL newlines it has, and repairs only a block that is
    predominantly escaped. An earlier version required zero real newlines
    and was defeated in a live run by a single trailing newline sitting
    outside the escaped body: 1 real vs 137 escaped, left unrepaired.

    A well-formed multi-line file has many real newlines and few or no
    escaped ones, so it is returned untouched. The minimum of 3 escaped
    pairs additionally stops a short snippet whose only escapes are
    deliberate string literals from ever being rewritten.
    """
    if not code:
        return code
    escaped_pairs = code.count("\\n")
    real_newlines = code.count("\n")
    if escaped_pairs < 3 or escaped_pairs <= real_newlines:
        return code
    # Ordered so an escaped backslash is restored last and doesn't get
    # re-interpreted as the lead-in of another sequence.
    for escaped, real in (("\\n", "\n"), ("\\t", "\t"), ('\\"', '"'), ("\\'", "'"), ("\\\\", "\\")):
        code = code.replace(escaped, real)
    return code


def round4_auto_turn(
    language: str,
    selected_design: list[dict],
    environment_code: str,
    current_code: str,
    conversation_so_far: list[dict],
    candidate_prompt: str,
) -> dict:
    """One AI turn. The deterministic pre-generation control runs FIRST -
    round4_auto_policy.is_prohibited - so a request to invent test cases,
    data, assertions or coverage never reaches the API at all."""
    if round4_auto_policy.is_prohibited(candidate_prompt):
        return {"response_kind": "refuse", "response_message": round4_auto_policy.REFUSAL_MESSAGE, "code_after": None}

    prompt = _load_prompt("round4_auto_turn.txt").format(
        language=language,
        selected_design=json.dumps(selected_design, indent=2),
        environment_code=environment_code,
        current_code=current_code,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        candidate_prompt=candidate_prompt,
    )
    raw = _call_claude(prompt, max_tokens=4096)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the automation turn, got: {type(result)}")
    parsed = Round4PilotTurnResponse.model_validate(result).model_dump()
    parsed["code_after"] = _repair_escaped_code(parsed.get("code_after"))
    return parsed


def score_round4_auto_conversation(
    language: str, selected_design: list[dict], refinements: list[dict], final_code: str,
    turns: list[dict], code_edits: list[dict], untraceable_literals: list[str],
    execution_result: dict, validation_text: str, ground_truth: str, validation_notes: str,
) -> dict:
    """Scores one automation submission against the 5-area rubric (20 each
    = 100 - see prompts/round4_auto_scoring.txt). Findings go through the
    SAME round4_evidence_audit backstop as every other round 4 flow (see
    scoring_service.score_round4_auto_submission). ground_truth/
    validation_notes are REFERENCE ONLY and never reach the candidate or
    the generator."""
    prompt_text = _load_prompt("round4_auto_scoring.txt")
    prompt = prompt_text.format(
        language=language,
        selected_design=json.dumps(selected_design, indent=2),
        refinements=json.dumps(refinements, indent=2) if refinements else "(none added)",
        final_code=final_code,
        turns_json=json.dumps(turns, indent=2),
        code_edits_json=json.dumps(code_edits, indent=2) if code_edits else "(the candidate made no direct edits of their own)",
        untraceable_literals=json.dumps(untraceable_literals, indent=2) if untraceable_literals else "(none)",
        execution_result_json=json.dumps(execution_result, indent=2),
        validation_text=validation_text or "(the candidate submitted no interpretation)",
        ground_truth=ground_truth,
        validation_notes=validation_notes,
    )
    raw = _call_claude(prompt, max_tokens=8192)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    if "findings" not in result:
        raise ValueError(f"Expected a 'findings' key in the automation scoring response, got keys: {list(result)}")
    validated = []
    for entry in result["findings"] or []:
        try:
            validated.append(Round4Finding.model_validate(entry).model_dump())
        except ValidationError:
            continue
    result["findings"] = validated
    result["_provenance"] = _scoring_provenance("round4_auto_scoring.txt", prompt_text)
    return result


# ---- Cross-round candidate summary (HR's candidate-detail view) ----
#
# Bulleted, not flowing paragraphs - HR's candidate-detail view and the
# exported PDF both render this as a scannable, enterprise-report-style
# breakdown (what went well / what was missed, per round, plus
# cross-round key observations and a short verdict) rather than dumping
# everything into undifferentiated prose. See schemas.CandidateSummary*
# for the exact shape this is validated against downstream.

def generate_candidate_summary(candidate_email: str, experience_band: str, rounds: list[dict]) -> dict:
    prompt = _load_prompt("candidate_summary_generation.txt").format(
        candidate_email=candidate_email,
        experience_band=experience_band,
        rounds_json=json.dumps(rounds, indent=2),
    )
    raw = _call_claude(prompt, max_tokens=2048)
    result = _parse_json_response(raw)
    if (
        not isinstance(result, dict)
        or "rounds" not in result or "key_observations" not in result or "verdict" not in result
    ):
        raise ValueError(
            f"Expected a JSON object with 'rounds', 'key_observations' and 'verdict' keys, got: {result!r}"
        )
    return result
