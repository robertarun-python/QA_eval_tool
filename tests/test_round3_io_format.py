"""
Round 3 input/output format - see services/round3_io_format.py. Run B
found the hidden tests read comma-separated input while the task never
said so, and the assistant answered "what format will the input come in?"
with "How should the input be read?". These pin the fix: one format source
used by the task screen, the hidden-test generator and the assistant; the
assistant repeats it when asked, never how to handle it in code.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Scenario
from app.services import llm_service, round3_io_format

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario, _seed_completed_rounds,
)

# Second-Largest Distinct Value's format, as migrate_round3_io_format states it.
from app.migrate_round3_io_format import IO_FORMAT as SECOND_LARGEST_FORMAT

# The exact question the Run B candidate asked on turn 1.
RUN_B_QUESTION = "What format will the input list come in? One line with numbers separated by spaces, or something else?"

# Anything that would tell the candidate HOW to handle the format in code.
_IMPLEMENTATION_RE = re.compile(r"split|int\s*\(|map\s*\(|input\s*\(|strip|pars|sys\.stdin|readline|scanner|```|\bfor\b", re.I)


def _no_model(*args, **kwargs):
    raise AssertionError("the format answer must not depend on the model")


def _turn(candidate_prompt, io_format=None, **kwargs):
    return llm_service.round3_coding_turn(
        scenario_description="Find the second-largest distinct value.", language="python",
        conversation_so_far=kwargs.get("conversation_so_far", []), current_code=kwargs.get("current_code"),
        candidate_prompt=candidate_prompt, turn_number=kwargs.get("turn_number", 1), io_format=io_format,
    )


# ---- the candidate asks for the format; the assistant repeats it ----

@pytest.mark.parametrize("question", [
    RUN_B_QUESTION,
    "What is the input format?",
    "How will the input be given?",
    "Are the numbers comma separated or space separated?",
    "what does the output look like?",
])
def test_format_question_is_answered_with_the_documented_format(monkeypatch, question):
    monkeypatch.setattr(llm_service, "_call_claude", _no_model)
    result = _turn(question, io_format=SECOND_LARGEST_FORMAT)
    assert result["response_kind"] == "explain"
    assert result["code_after"] is None
    assert SECOND_LARGEST_FORMAT["input"] in result["response_message"]
    assert SECOND_LARGEST_FORMAT["output"] in result["response_message"]


def test_format_answer_gives_no_implementation(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", _no_model)
    message = _turn(RUN_B_QUESTION, io_format=SECOND_LARGEST_FORMAT)["response_message"]
    # Strip the quoted format itself - it legitimately says "comma-separated".
    rest = message.replace(round3_io_format.as_text(SECOND_LARGEST_FORMAT), "")
    assert not _IMPLEMENTATION_RE.search(rest)
    assert not _IMPLEMENTATION_RE.search(round3_io_format.as_text(SECOND_LARGEST_FORMAT))


def test_format_answer_never_asks_the_question_back(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", _no_model)
    message = _turn(RUN_B_QUESTION, io_format=SECOND_LARGEST_FORMAT)["response_message"]
    assert "how should the input be read" not in message.lower()


def test_format_answer_uses_the_default_when_no_format_is_passed(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", _no_model)
    message = _turn("What is the input format?")["response_message"]
    assert round3_io_format.DEFAULT_IO_FORMAT["input"] in message


# ---- anything else still goes to the model, unchanged ----

@pytest.mark.parametrize("instruction", [
    "Read one line of input, split it on commas, convert each part to int, and store it in a list called nums.",
    "What format is the input? Then store it in a list called nums.",  # a question bundled with an instruction
    "Sort nums from largest to smallest.",
    "Why does max() crash on an empty input?",
    "What does the list look like after sorting?",
])
def test_non_format_messages_still_reach_the_model(monkeypatch, instruction):
    calls = []

    def fake_claude(prompt, max_tokens=4096):
        calls.append(prompt)
        return json.dumps({"response_kind": "explain", "response_message": "model reply", "code_after": None})

    monkeypatch.setattr(llm_service, "_call_claude", fake_claude)
    result = _turn(instruction, io_format=SECOND_LARGEST_FORMAT)
    assert calls, "expected the model to be called"
    assert result["response_message"] == "model reply"


def test_model_prompt_carries_the_format_and_the_repeat_only_rule(monkeypatch):
    prompts = []

    def fake_claude(prompt, max_tokens=4096):
        prompts.append(prompt)
        return json.dumps({"response_kind": "explain", "response_message": "ok", "code_after": None})

    monkeypatch.setattr(llm_service, "_call_claude", fake_claude)
    _turn("Sort nums from largest to smallest.", io_format=SECOND_LARGEST_FORMAT)
    prompt = prompts[0]
    assert round3_io_format.as_text(SECOND_LARGEST_FORMAT) in prompt
    assert "repeat the format above" in prompt
    assert "Never say how to read, split, parse or convert it" in prompt
    assert "Never state, assume or suggest any format other than the one above" in prompt


def test_code_edit_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "response_kind": "code_edit", "response_message": "Read the line into nums.",
        "code_after": "nums = [int(x) for x in input().split(',')]",
    }))
    result = _turn("Read one line, split it on commas, convert each part to int, store it in nums.", io_format=SECOND_LARGEST_FORMAT)
    assert result["response_kind"] == "code_edit"
    assert "split(',')" in result["code_after"]


# ---- the task and the hidden-test generator use the same format ----

def test_task_and_generator_use_the_same_format(monkeypatch):
    scenario = Scenario(round_number=3, title="t", description="d", config_json={round3_io_format.CONFIG_KEY: SECOND_LARGEST_FORMAT})
    shown = scenario.round3_io_format
    prompts = []

    def fake_claude(prompt, max_tokens=4096):
        prompts.append(prompt)
        return json.dumps({
            "test_cases": [{"input": "3,1,4", "expected_output": "3", "description": "basic"}],
            "expected_approach": "x", "reference_solution": "print(3)",
        })

    monkeypatch.setattr(llm_service, "_call_claude", fake_claude)
    llm_service.generate_round3_reference(scenario_description="d", experience_band="0-7", io_format=shown)
    assert f"Input: {shown['input']}" in prompts[0]
    assert f"Output: {shown['output']}" in prompts[0]
    assert "comma-separated values on a single line, matching how" not in prompts[0]  # the old hard-coded copy


def test_generator_rejects_hidden_tests_in_a_different_format(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude", lambda prompt, max_tokens=4096: json.dumps({
        "test_cases": [{"input": "3 1 4", "expected_output": "3", "description": "space-separated"}],
        "expected_approach": "x", "reference_solution": "print(3)",
    }))
    with pytest.raises(ValueError):
        llm_service.generate_round3_reference(scenario_description="d", experience_band="0-7", io_format=SECOND_LARGEST_FORMAT)


def test_hr_generation_passes_the_scenarios_own_format(client, monkeypatch):
    seen = {}

    def fake_generate(**kwargs):
        seen.update(kwargs)
        return {"test_cases": [], "expected_approach": "x", "reference_solution": "print(1)"}

    monkeypatch.setattr(llm_service, "generate_round3_reference", fake_generate)
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    client.post(
        "/hr/scenarios",
        json={"round_number": 3, "title": "t", "description": "d", "experience_band": "0-7", "time_limit_minutes": 30,
              "config_json": {round3_io_format.CONFIG_KEY: SECOND_LARGEST_FORMAT}},
        cookies=_auth(hr_token),
    )
    assert seen["io_format"] == round3_io_format.for_config({round3_io_format.CONFIG_KEY: SECOND_LARGEST_FORMAT})


def test_integer_format_accepts_comma_separated_integers_only():
    # Invented inputs - the live task's own hidden tests stay out of the repo
    # (the migration checks those against the format when it runs).
    for ok in ["4,8,15", "16", "", "-23,-23,42", "0,0", "7,7,7,7", "100000,-100000"]:
        assert round3_io_format.input_conforms(ok, SECOND_LARGEST_FORMAT), ok
    for bad in ["4 8 15", "4, 8, 15", "4,8,x", "1.5,2", "4,8\n15", "4,,8"]:
        assert not round3_io_format.input_conforms(bad, SECOND_LARGEST_FORMAT), bad


def test_format_is_round3_only():
    assert Scenario(round_number=1, title="t", description="d", config_json={}).round3_io_format is None
    assert Scenario(round_number=3, title="t", description="d", config_json={}).round3_io_format == round3_io_format.DEFAULT_IO_FORMAT


# ---- the candidate's screen shows it, and the turn route passes it on ----

def _candidate_in_round3(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="R1")
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="R2")
    scenario = _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Second-Largest Distinct Value")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"})
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post("/candidate/round/1/submit", json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]}, cookies=_auth(cand_token))
    _seed_completed_rounds(CANDIDATE1_EMAIL, 2)
    assert client.post("/candidate/round/3/start", json={}, cookies=_auth(cand_token)).status_code == 201
    return scenario, cand_token


def test_candidate_task_shows_the_format_and_turns_receive_it(client, monkeypatch):
    scenario, cand_token = _candidate_in_round3(client, monkeypatch)
    import app.database as database_module
    db = database_module.SessionLocal()
    row = db.get(Scenario, scenario["id"])
    row.config_json = {**(row.config_json or {}), round3_io_format.CONFIG_KEY: SECOND_LARGEST_FORMAT}
    db.commit()
    db.close()

    shown = client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()["scenario"]["round3_io_format"]
    assert shown == SECOND_LARGEST_FORMAT
    assert "reference_json" not in client.get("/candidate/round/3/state", cookies=_auth(cand_token)).json()["scenario"]

    seen = {}

    def fake_turn(**kwargs):
        seen.update(kwargs)
        return {"response_kind": "explain", "response_message": "ok", "code_after": None}

    monkeypatch.setattr(llm_service, "round3_coding_turn", fake_turn)
    client.post("/candidate/round/3/turn", json={"candidate_prompt": RUN_B_QUESTION}, cookies=_auth(cand_token))
    assert seen["io_format"] == shown


# ---- the data migration for the existing task ----

def _file_db(tmp_path, monkeypatch, reference_inputs):
    url = f"sqlite:///{tmp_path / 'm.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    db.add(Scenario(
        round_number=3, title="Second-Largest Distinct Value", description="d", created_by=1,
        config_json={}, reference_json={"test_cases": [{"input": i, "expected_output": "0", "description": ""} for i in reference_inputs]},
    ))
    db.commit()
    db.close()
    from app.config import settings
    monkeypatch.setattr(settings, "database_url", url)
    return engine


def test_migration_states_the_format_once(tmp_path, monkeypatch):
    from app import migrate_round3_io_format
    engine = _file_db(tmp_path, monkeypatch, ["4,8,15", "", "16"])
    assert migrate_round3_io_format.migrate() == ["scenarios.1.config_json.round3_io_format"]
    assert migrate_round3_io_format.migrate() == []  # idempotent
    row = sessionmaker(bind=engine)().get(Scenario, 1)
    assert row.round3_io_format == round3_io_format.for_config({round3_io_format.CONFIG_KEY: SECOND_LARGEST_FORMAT})
    assert "comma-separated integers" in row.round3_io_format["input"]


def test_migration_refuses_when_hidden_tests_disagree(tmp_path, monkeypatch):
    from app import migrate_round3_io_format
    _file_db(tmp_path, monkeypatch, ["3 1 4"])
    with pytest.raises(ValueError):
        migrate_round3_io_format.migrate()
