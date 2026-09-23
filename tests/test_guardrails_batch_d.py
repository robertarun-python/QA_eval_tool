"""
Batch D of the Sep 2026 assistant guardrail fixes:

- R3 no longer asks for variable names or message wording - the largest
  source of needless questions in the transcripts ("What should I name
  the variable?", "What message should I display?" asked up to 4 times).
- The syntax-fix path checks JavaScript and Java syntax for real too,
  not just Python.
"""
import json

import pytest

from app.services import execution_service, llm_service

_PROMPT = llm_service._load_prompt("round3_coding_turn.txt")


def test_r3_prompt_no_longer_asks_for_names():
    assert "A name for anything new" not in _PROMPT
    assert "invent a NAME" not in _PROMPT
    assert 'never ask "what should I name it?"' in _PROMPT


def test_r3_prompt_writes_neutral_message_text_instead_of_asking():
    assert "never ask what it should say" in _PROMPT
    assert "Never rename anything the candidate named themselves" in _PROMPT


def test_r3_prompt_still_asks_about_techniques():
    assert "choose a MECHANICAL TECHNIQUE the candidate didn't state" in _PROMPT


needs_node = pytest.mark.skipif(not execution_service.toolchain_available("javascript"), reason="node not installed")
needs_jdk = pytest.mark.skipif(not execution_service.toolchain_available("java"), reason="JDK not installed")


@needs_node
def test_javascript_syntax_error_is_found():
    checked, error = execution_service.syntax_error("javascript", "const x = 1;\nconsole.log(x")
    assert checked and "line 2" in error


@needs_node
def test_valid_javascript_passes():
    assert execution_service.syntax_error("javascript", "const x = 1;\nconsole.log(x);") == (True, None)


@needs_jdk
def test_java_syntax_error_is_found():
    code = "public class Main {\n  public static void main(String[] a) {\n    System.out.println(1)\n  }\n}"
    checked, error = execution_service.syntax_error("java", code)
    assert checked and "';' expected" in error and "line 3" in error


@needs_jdk
@pytest.mark.parametrize("code", [
    # compiles fine
    "public class Main {\n  public static void main(String[] a) {\n    System.out.println(1);\n  }\n}",
    # a compile error, but not a syntax error - the syntax fixer must not claim one
    "public class Main {\n  public static void main(String[] a) {\n    System.out.println(missing);\n  }\n}",
    # a public class not named Main still parses
    "public class Solution {\n  public static void main(String[] a) {\n    System.out.println(1);\n  }\n}",
])
def test_java_code_that_parses_is_not_reported_as_a_syntax_error(code):
    assert execution_service.syntax_error("java", code) == (True, None)


def test_unavailable_toolchain_means_the_model_answer_stands(monkeypatch):
    monkeypatch.setattr(execution_service, "toolchain_available", lambda language: False)
    assert execution_service.syntax_error("java", "anything") == (False, None)
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.", "code_after": "anything",
        "category_status": {},
    }))
    result = llm_service.round3_syntax_fix(code="anything", language="java", required_constructs=[], declared_constructs={})
    assert result["response_message"] == "No syntax issues found."


@needs_node
def test_false_no_syntax_issues_claim_is_corrected_for_javascript(monkeypatch):
    code = "console.log(1"
    monkeypatch.setattr(llm_service, "_call_claude", lambda p, max_tokens=4096: json.dumps({
        "response_kind": "direct_edit", "response_message": "No syntax issues found.", "code_after": code,
        "category_status": {},
    }))
    result = llm_service.round3_syntax_fix(code=code, language="javascript", required_constructs=[], declared_constructs={})
    assert result["code_after"] == code
    assert "doesn't parse yet" in result["response_message"]
