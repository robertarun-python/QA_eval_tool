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
from ..schemas import Round4TurnResponse, Round4EnvironmentOut, Round4UiMockupOut, Round3CodingTurnResponse
from . import round3_constructs

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
        # feedback for however long the default allows.
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=30.0)
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
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


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
    if not isinstance(result, dict) or "test_cases" not in result or "expected_approach" not in result:
        raise ValueError(f"Expected a JSON object with 'test_cases' and 'expected_approach' keys, got: {result!r}")
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
) -> dict:
    prompt = _load_prompt("round3_coding_turn.txt").format(
        scenario_description=scenario_description,
        language=language,
        conversation_so_far=json.dumps(conversation_so_far, indent=2),
        current_code=current_code or "(no code written yet)",
        candidate_prompt=candidate_prompt,
        turn_number=turn_number,
        is_first_turn="true" if turn_number == 1 else "false",
    )
    raw = _call_claude(prompt, max_tokens=2048)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for the assistant's turn, got: {type(result)}")
    try:
        return Round3CodingTurnResponse.model_validate(result).model_dump()
    except ValidationError as e:
        raise ValueError(f"Assistant's turn response didn't match the expected shape: {e}") from e


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


def score_round4_conversation(round1_context: dict, test_cases: list[dict], assistance_pct: int) -> dict:
    prompt_text = _load_prompt("round4_scoring.txt")
    prompt = prompt_text.format(
        round1_scenario_title=round1_context["scenario_title"],
        round1_scenario_description=round1_context["scenario_description"],
        round1_submitted_rows=json.dumps(round1_context["submitted_rows"], indent=2),
        assistance_pct=assistance_pct,
        test_cases_json=json.dumps(test_cases, indent=2),
    )
    raw = _call_claude(prompt, max_tokens=8192)  # covers every test case's transcript + feedback
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
    result["_provenance"] = _scoring_provenance("round4_scoring.txt", prompt_text)
    return result


# ---- Cross-round candidate summary (HR's candidate-detail view) ----
#
# Structured, not one flowing paragraph - HR's candidate-detail view and
# the exported PDF both render this as a labeled per-round breakdown
# (Round 1 comment, Round 2 comment, ...) followed by a final verdict,
# matching how every other feedback surface in this app is laid out
# (score / coverage / feedback / misses, clearly labeled) rather than
# dumping everything into undifferentiated prose.

def generate_candidate_summary(candidate_email: str, experience_band: str, rounds: list[dict]) -> dict:
    prompt = _load_prompt("candidate_summary_generation.txt").format(
        candidate_email=candidate_email,
        experience_band=experience_band,
        rounds_json=json.dumps(rounds, indent=2),
    )
    raw = _call_claude(prompt, max_tokens=1536)
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or "rounds" not in result or "final_summary" not in result:
        raise ValueError(f"Expected a JSON object with 'rounds' and 'final_summary' keys, got: {result!r}")
    return result
