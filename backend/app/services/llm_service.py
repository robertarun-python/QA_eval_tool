"""
Every call to the Claude API goes through this file. Two reasons to
centralize it rather than calling `anthropic.Anthropic()` from inside
routers: (1) one place to change models/retry logic later, (2) prompts
live in text files under app/prompts/, loaded here, so the actual
wording is easy to find and edit without touching Python.
"""
import json
from pathlib import Path

import anthropic

from ..config import settings

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
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


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
    prompt = _load_prompt("round1_scoring.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
        reference_cases=json.dumps(reference_cases, indent=2),
        candidate_submission=candidate_submission,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object for scoring, got: {type(result)}")
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
    prompt = _load_prompt("round2_debug_scoring.txt").format(
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
    return result


# ---- Round 3 ----
#
# Conversational, not one-shot: the candidate prompts an assistant to
# build test automation for their OWN round 1 scenario+answer, across
# as many self-titled test cases as they choose to write (no fixed
# UI/API/DB categories - see models.Round3TestCase; deciding what to
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
# round3_partial_response.txt) - both would just be different flavors of
# hinting, which is exactly what this round is trying not to do. HR's
# per-scenario "rules" (currently just assistance level) are a future
# authoring UI; for now every round 3 scenario uses these defaults,
# merged over whatever Scenario.config_json HR happens to have set. The
# fallback value itself lives in config.py (round3_default_assistance_pct),
# not here - see that file's docstring for why.
DEFAULT_ROUND3_CONFIG = {
    "assistance_pct": settings.round3_default_assistance_pct,
}


def generate_round3_environment(app_description: str) -> dict:
    """Auto-generates fictional test-environment reference facts (test
    login credentials, API endpoints, a DB schema reference, ...) for a
    round 3 scenario - shown to every candidate served this scenario.
    Grounded in the same app_description as generate_round3_ui_mockup
    (whichever Round 1 scenario is live for this band, resolved by the
    caller - see hr.py's _generate_reference) rather than round 3's own
    description, which is just instructions to the candidate, not a
    description of the app under test - generic grounding here produced
    generic, unhelpful credentials/schema before this was fixed."""
    prompt = _load_prompt("round3_environment_generation.txt").format(
        app_description=app_description,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or "fields" not in result:
        raise ValueError(f"Expected a JSON object with a 'fields' key, got: {result!r}")
    return result


def generate_round3_ui_mockup(app_description: str) -> dict:
    """Auto-generates a structured (never raw HTML) reference sketch of
    the app's screens - shown to the candidate as a static visual
    reference, the way a real QA automation engineer would have the
    actual app open in a browser. Grounded in whichever Round 1 scenario
    is live for this band (the actual "app" a candidate's round 1
    answer/round 3 automation is about) - see hr.py's _generate_reference
    for that lookup; app_description is already resolved by the caller,
    this function doesn't know or care where it came from."""
    prompt = _load_prompt("round3_ui_mockup_generation.txt").format(
        app_description=app_description,
    )
    raw = _call_claude(prompt)
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or "screens" not in result:
        raise ValueError(f"Expected a JSON object with a 'screens' key, got: {result!r}")
    return result


def round3_respond(
    test_case_title: str,
    environment: dict | None,
    scenario_instructions: str,
    round1_context: dict,
    conversation_so_far: list[dict],
    candidate_prompt: str,
    assistance_pct: int,
    turn_number: int,
) -> dict:
    prompt = _load_prompt("round3_partial_response.txt").format(
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
    return result


def score_round3_conversation(round1_context: dict, test_cases: list[dict], assistance_pct: int) -> dict:
    prompt = _load_prompt("round3_scoring.txt").format(
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
