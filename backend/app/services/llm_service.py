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

def generate_round1_reference(scenario_description: str, experience_band: str) -> list[dict]:
    prompt = _load_prompt("round1_reference_generation.txt").format(
        scenario_description=scenario_description,
        experience_band=experience_band,
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


# ---- Round 2 (stub) ----

def score_round2_debugging(scenario_description: str, experience_band: str, candidate_submission: str) -> dict:
    # TODO(round2): implement using the same two-call pattern as round 1
    # (generate a reference debugging approach, then score against it).
    # See backend/app/prompts/round2_debug_scoring.txt.
    raise NotImplementedError("Round 2 scoring is not implemented yet - see TODO(round2) comments.")


# ---- Round 3 (stub) ----

def round3_respond(scenario_description: str, conversation_so_far: list[dict], candidate_prompt: str) -> str:
    # TODO(round3): implement the deliberately-partial-response model
    # call described in ARCHITECTURE.md. See
    # backend/app/prompts/round3_partial_response.txt.
    raise NotImplementedError("Round 3 is not implemented yet - see TODO(round3) comments.")


def score_round3_conversation(conversation_turns: list[dict]) -> dict:
    # TODO(round3): score the *conversation quality*, not just the final
    # output - see design notes in ARCHITECTURE.md.
    raise NotImplementedError("Round 3 scoring is not implemented yet - see TODO(round3) comments.")
