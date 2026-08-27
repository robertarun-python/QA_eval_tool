"""
Round 3's fixed construct taxonomy - see
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.
Pure data + pure functions, no LLM involved.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.services import round3_constructs


def test_forbidden_vocab_combines_generic_and_language_specific():
    words = round3_constructs.forbidden_vocab("collection", "python")
    assert "data structure" in words  # generic
    assert "list" in words            # python-specific


def test_forbidden_vocab_is_scoped_to_the_active_language():
    words = round3_constructs.forbidden_vocab("collection", "java")
    assert "list" not in words        # python-only keyword
    assert "arraylist" in words


def test_forbidden_vocab_for_unknown_language_is_generic_only():
    words = round3_constructs.forbidden_vocab("iteration", "cobol")
    assert "loop" in words
    assert "for" not in words  # no language-specific set exists for "cobol"


def test_contains_forbidden_vocab_catches_a_leak_case_insensitively():
    assert round3_constructs.contains_forbidden_vocab(
        "Should this be a List or a Dict?", "collection", "python"
    )


def test_contains_forbidden_vocab_passes_a_clean_neutral_question():
    assert not round3_constructs.contains_forbidden_vocab(
        "How do you want to represent and hold onto that information?", "collection", "python"
    )


def test_contains_forbidden_vocab_is_scoped_to_the_active_language():
    # "arraylist" is a Java leak, not a Python one - a Python candidate's
    # question is never penalized for another language's vocabulary.
    assert not round3_constructs.contains_forbidden_vocab(
        "How should this be represented as an ArrayList?", "collection", "python"
    )


def test_every_category_has_a_fallback_question():
    for category in round3_constructs.CONSTRUCT_CATEGORIES:
        assert category in round3_constructs.FALLBACK_QUESTIONS
        assert round3_constructs.FALLBACK_QUESTIONS[category]


def test_fallback_questions_never_leak_their_own_category():
    # The safety-net template itself must obey the same rule it enforces.
    for category in round3_constructs.CONSTRUCT_CATEGORIES:
        question = round3_constructs.FALLBACK_QUESTIONS[category]
        for language in ("python", "java", "javascript"):
            assert not round3_constructs.contains_forbidden_vocab(question, category, language)
