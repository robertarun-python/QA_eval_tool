"""
Round 3 - the single source of truth for a task's stdin/stdout format.

Run B review found the hidden tests read comma-separated input while the
candidate-facing task never said so, and the assistant answered "what's
the input format?" with a question of its own - a technically correct
candidate failed 13/15 hidden tests on the separator alone. The format is
a fixed fact of the task, not a design decision the candidate is being
assessed on, so it now lives here and is used, verbatim, by:
  - the reference/hidden-test generator (llm_service.generate_round3_reference),
  - the candidate's task screen (models.Scenario.round3_io_format),
  - the assistant's turn prompt and its deterministic format answer
    (llm_service._round3_coding_turn_once).
Knowing the format is not the same as knowing how to handle it in code -
nothing here ever names a function or technique (split, int(), ...).

A task can override the default through
Scenario.config_json["round3_io_format"] = {"input": ..., "output": ...,
"value_type": "integers" | "values"}. No LLM call anywhere in this module.
"""
import re

SEPARATOR = ","

DEFAULT_IO_FORMAT = {
    "input": "One line of comma-separated values (no spaces), read from standard input. An empty line means there are no values.",
    "output": "Print only the answer, on a single line, to standard output - no labels or extra text.",
    "value_type": "values",
}

CONFIG_KEY = "round3_io_format"


def for_config(config_json: dict | None) -> dict:
    """The task's format: its own override if HR/a migration set one,
    else the default every Round 3 reference has always been generated with."""
    override = (config_json or {}).get(CONFIG_KEY) or {}
    fmt = dict(DEFAULT_IO_FORMAT)
    fmt.update({k: v for k, v in override.items() if k in DEFAULT_IO_FORMAT and v})
    return fmt


def as_text(fmt: dict) -> str:
    return f"Input: {fmt['input']}\nOutput: {fmt['output']}"


def input_conforms(test_input: str, fmt: dict) -> bool:
    """Whether a hidden-test stdin matches the stated format - used to
    reject a generated reference that drifted from what the candidate is
    told. Only "integers" is checked token by token; free-form "values"
    only has to be one line."""
    text = (test_input or "").rstrip("\n")
    if "\n" in text:
        return False
    if fmt.get("value_type") != "integers" or text == "":
        return True
    return all(re.fullmatch(r"-?\d+", token) for token in text.split(SEPARATOR))


# A question about the format itself ("what format will the input come
# in?", "what does the output look like?"). Deliberately narrow: it has to
# be a question, name input/output, name the format, and carry no
# instruction to write or change code - anything else still goes to the
# model, whose prompt has the same format and the same repeat-only rule.
_QUESTION_RE = re.compile(r"\?|^\s*(what|how|which|is|are|does|do|will|in what)\b", re.I)
_SUBJECT_RE = re.compile(r"\b(input|output|stdin|stdout)\b", re.I)
_FORMAT_RE = re.compile(
    r"\bformat|\bseparat|\bdelimit|\bcomma|\bspace|\bcome in\b|\blook like\b|\bgiven\b|\bprovided\b|\bone line\b|\bper line\b|\bshape\b",
    re.I,
)
# "Are the numbers comma separated?" names the values, not "input" - only
# counted with an explicit separator/format word, so "what does the list
# look like after sorting?" still goes to the model.
_VALUES_SUBJECT_RE = re.compile(r"\b(numbers|values|integers|list|data)\b", re.I)
_STRONG_FORMAT_RE = re.compile(r"\bformat|\bseparat|\bdelimit|\bcomma|\bspaces?\b", re.I)
_INSTRUCTION_RE = re.compile(
    r"\b(write|add|create|store|put|make|change|remove|replace|implement|convert|split|loop|then|code)\b", re.I
)


def is_format_question(candidate_prompt: str) -> bool:
    text = (candidate_prompt or "").strip()
    return bool(
        text
        and len(text) <= 300
        and _QUESTION_RE.search(text)
        and (
            (_SUBJECT_RE.search(text) and _FORMAT_RE.search(text))
            or (_VALUES_SUBJECT_RE.search(text) and _STRONG_FORMAT_RE.search(text))
        )
        and not _INSTRUCTION_RE.search(text)
    )


def format_answer(fmt: dict) -> str:
    return (
        "The task specifies it:\n"
        f"{as_text(fmt)}\n"
        "How your code handles that is your decision - tell me the step you want and I'll write exactly that."
    )
