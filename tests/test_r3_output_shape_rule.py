"""
Round 3: the published output format fixes the SHAPE of what is printed, never WHICH value - the candidate's
complete method decides that (policy, category C = option 1, 2026-10-08). Real-AI gate 11, with the live
scenario's own format ("Print a single integer on one line - the second-largest distinct value, or -1 - ..."):
"remove duplicates, sort descending, and return the first element" got "What should the program output?" twice
- the format rule told the model to print "the answer" in a format that names the answer. Both the prompt and
the steering retry note now say the format constrains shape only. Scripted model; no AI call.
"""
import json
import re

from app.services import llm_service

TASK = ("Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the list "
        "(duplicates of the largest value do not count as a separate value). If the list has fewer than two distinct "
        "values, print -1 instead.")
LIVE_FORMAT = {  # the live Round 3 scenario's published format, verbatim
    "input": "One line of comma-separated integers (no spaces), read from standard input. An empty line means an empty list.",
    "output": "Print a single integer on one line - the second-largest distinct value, or -1 - with no labels or extra text.",
    "value_type": "integers",
}
CASE1 = "Write the program using my approach: remove duplicates, sort descending, and return the first element."
PROMPT_RULE = ("The output format fixes how the answer is printed (its shape). When the candidate's steps say what to output, "
               "print exactly what their steps produce, in that published shape, even if the format description names a "
               "different value.")
NOTE_RULE = ("Print what the candidate's steps produce, using the published output shape, even if the output format "
             "describes a different value.")
# The steering retry's code: their method, the published empty-line read, one integer printed - nothing else.
THEIR_METHOD = ("line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\n"
                "unique = list(set(numbers))\nunique.sort(reverse=True)\nprint(unique[0])\n")


def _reply(kind, msg, code=None):
    return json.dumps({"response_kind": kind, "response_message": msg, "code_after": code, "category_status": {}})


def _turn(monkeypatch, replies, prompt=CASE1, io_format=LIVE_FORMAT):
    calls, queue = [], list(replies)

    def fake(p, max_tokens=4096):
        calls.append(p)
        return queue.pop(0) if queue else _reply("clarify", "What should the program output?")
    monkeypatch.setattr(llm_service, "_call_claude", fake)
    result = llm_service.round3_coding_turn(scenario_description=TASK, language="python", conversation_so_far=[],
                                            current_code=None, candidate_prompt=prompt, turn_number=1, io_format=io_format)
    return result, calls


def test_the_prompt_rule_follows_the_format_rule():
    lines = [line for line in llm_service._load_prompt("round3_coding_turn.txt").splitlines() if line.strip()]
    i = next(k for k, line in enumerate(lines) if line.startswith("Format rule: this format is a fact of the task"))
    assert lines[i + 1] == PROMPT_RULE


def test_the_steering_note_carries_the_same_rule():
    assert llm_service._R3_APPROACH_NOTE.endswith(NOTE_RULE + "")
    assert "Do not handle any input case they have not raised." in llm_service._R3_APPROACH_NOTE  # unchanged


def test_neither_rule_names_any_task_content():
    for text in (PROMPT_RULE, NOTE_RULE):
        assert not [w for w in ("second", "distinct", "largest", "-1", "duplicate") if w in text.lower()]


def test_case1_under_the_live_format_gets_their_method(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?"),
                                        _reply("code_edit", "Done.", THEIR_METHOD)])
    # the question is caught; the steering retry (with the shape rule) writes their method; two calls, no third
    assert len(calls) == 2 and llm_service._R3_APPROACH_NOTE in calls[1] and NOTE_RULE in calls[1]
    assert PROMPT_RULE in calls[0] and PROMPT_RULE in calls[1]
    assert LIVE_FORMAT["output"] in calls[0]  # the format is still given to the model as published
    code = result["code_after"]
    assert result["response_kind"] == "code_edit" and code == THEIR_METHOD
    assert "print(unique[0])" in code and "[1]" not in code          # first element - not changed to the second
    assert not re.search(r"-\s?1\b", code)                            # no invented fallback
    assert llm_service.r3_unraised_case_in_code(None, code, [], CASE1, LIVE_FORMAT["input"]) is None  # read only
    assert code.count("print(") == 1                                  # one value, one line: the published shape


def test_a_steering_retry_that_switches_to_the_published_value_is_not_their_method(monkeypatch):
    """The test above pins the expected delivery; this pins that the AI's own correction would be visible as such."""
    corrected = THEIR_METHOD.replace("print(unique[0])", "print(unique[1] if len(unique) > 1 else -1)")
    assert llm_service.r3_unraised_case_in_code(None, corrected, [], CASE1, LIVE_FORMAT["input"]) is not None
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?"),
                                        _reply("code_edit", "Done.", corrected), _reply("code_edit", "Done.", corrected)])
    assert result["code_after"] is None and len(calls) == 3  # caught; the one targeted repair; never a fourth call


def test_no_third_corrective_retry_when_the_retry_asks_again(monkeypatch):
    result, calls = _turn(monkeypatch, [_reply("clarify", "What should the program output?")] * 4)
    assert len(calls) == 2 and result["code_after"] is None
    assert result["response_message"] in llm_service.R3_PROGRESS_MESSAGES


def test_a_candidate_output_instruction_wins_over_the_described_value(monkeypatch):
    """Category C: their stated output is printed, in the published shape, though the format names another value."""
    steps = "Read the numbers, find the smallest value with min(), and print it."
    code = "line = input()\nif line:\n    numbers = [int(x) for x in line.split(',')]\nelse:\n    numbers = []\nprint(min(numbers))\n"
    result, calls = _turn(monkeypatch, [_reply("code_edit", "Done.", code)], prompt=steps)
    assert result["code_after"] == code and len(calls) == 1
    assert PROMPT_RULE in calls[0]
