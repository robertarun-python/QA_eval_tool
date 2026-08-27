"""
Round 3's fixed construct taxonomy: the categories a coding problem's
solution can require, plus the vocabulary that would leak each one if the
assistant's clarifying question named it. See
docs/superpowers/specs/2026-08-27-round3-construct-checklist-design.md.

This module ships the categories Round 3's current problem set actually
exercises (procedural: variables, collections, iteration, conditionals,
functions, I/O). It's a plain data module by design - extending it to
OOP/async/recursion categories later is a data-only change, not a
redesign; nothing that consumes this module cares how many entries exist.
"""
import re

CONSTRUCT_CATEGORIES = [
    "variable", "collection", "element_access",
    "iteration", "nested_iteration",
    "condition", "comparison", "boolean_logic",
    "function", "parameter", "return_value",
    "arithmetic_operation", "string_operation", "type_conversion",
    "input", "output",
]

# Language-neutral trigger words - forbidden regardless of which language
# is active, because they name the CONCEPT ("loop", "iterate") rather
# than any one language's syntax for it.
GENERIC_VOCAB = {
    "variable": {"variable", "name it", "call it"},
    "collection": {"collection", "data structure", "group of values"},
    "element_access": {"index", "indexing", "element access", "position in the"},
    "iteration": {"loop", "iterate", "iteration", "repeat", "cycle through", "go through each"},
    "nested_iteration": {"nested loop", "inner loop", "outer loop", "nested"},
    "condition": {"condition", "conditional", "decision", "branch"},
    "comparison": {"compare", "comparison", "comparison operator"},
    # "and"/"or"/"not" are deliberately NOT listed bare here - they are
    # ordinary English function words ("before", "hold, or", "handle"),
    # and a bare-word block on them would make almost any natural
    # sentence about this category unphraseable. The multi-word phrases
    # below still catch the LLM naming the concept outright; Java/JS
    # additionally get the unambiguous operator symbols in
    # LANGUAGE_CONSTRUCTS. Python's mechanical coverage here is
    # intentionally weaker for this one category - see the design spec.
    "boolean_logic": {"boolean logic", "combine conditions", "both conditions", "either condition"},
    "function": {"function", "method", "define a function", "subroutine"},
    "parameter": {"parameter", "argument", "input to the function"},
    "return_value": {"return", "return value", "return statement"},
    "arithmetic_operation": {"arithmetic operation", "add", "subtract", "multiply", "divide"},
    "string_operation": {"string operation", "concatenate", "substring", "string method"},
    "type_conversion": {"convert", "conversion", "cast", "type conversion", "parse"},
    "input": {"input", "read input", "read from"},
    "output": {"output", "print", "display the result"},
}

# Per-language literal keywords/idioms. Only the active submission's
# language layer is ever checked - a Java candidate is never penalized
# for Python's "def" leaking, because it's simply not in their set.
LANGUAGE_CONSTRUCTS = {
    "python": {
        # "list"/"dict"/"tuple" stay bare - they're specific enough to
        # the collection concept that the risk of colliding with
        # ordinary English is accepted (same tradeoff as elsewhere in
        # this module). Bare "set" is a genuinely common English word
        # ("set the value", "a set of two") - the parenthesized
        # constructor form still catches a real leak without that
        # collision, matching the "int(" pattern already used below.
        "collection": {"list", "tuple", "dict", "dictionary", "set("},
        "element_access": {"square brackets"},
        # Bare "for"/"while" are ordinary English words too common to
        # block outright (see the multi-word forms kept below, and the
        # GENERIC_VOCAB "loop"/"iterate" layer that still applies).
        "iteration": {"for loop", "while loop", "range("},
        # Bare "if" and "else" are ordinary English; "elif" alone is
        # unambiguous and Python-specific.
        "condition": {"elif"},
        "comparison": {"==", "!=", ">=", "<="},
        "boolean_logic": set(),  # see the GENERIC_VOCAB comment above
        "function": {"def", "lambda"},
        "return_value": {"return"},
        # Bare arithmetic symbols removed - "-"/"/" collide with
        # ordinary punctuation (a hyphen, a slash) in any neutral
        # question; GENERIC_VOCAB's "add"/"subtract"/"multiply"/
        # "divide" already catches every realistic natural-language leak.
        "arithmetic_operation": set(),
        "string_operation": {".join(", ".split(", "f-string"},
        "type_conversion": {"int(", "str(", "float(", "list("},
        "input": {"input("},
        "output": {"print("},
    },
    "java": {
        "collection": {"array", "arraylist", "hashmap", "hashset", "linkedlist"},
        "element_access": {"square brackets", ".get("},
        "iteration": {"do-while", "enhanced-for", "for-each"},
        "condition": {"else if", "switch"},
        "comparison": {"==", "!=", ">=", "<=", ".equals("},
        "boolean_logic": {"&&", "||"},
        "function": {"method", "public", "private", "static"},
        "return_value": {"return"},
        "arithmetic_operation": set(),
        "string_operation": {".concat(", ".substring(", "stringbuilder"},
        "type_conversion": {"(int)", "(double)", "integer.parseint", "string.valueof"},
        "input": {"scanner", "system.in", "bufferedreader"},
        "output": {"system.out.println", "system.out.print"},
    },
    "javascript": {
        # Bare "object"/"map"/"set" are ordinary English words; "array"
        # is specific enough to keep.
        "collection": {"array"},
        "element_access": {"square brackets"},
        "iteration": {"for-of", "for-in", "foreach", "for each"},
        "condition": {"else if", "switch"},
        "comparison": {"===", "!==", "==", "!=", ">=", "<="},
        "boolean_logic": {"&&", "||"},
        "function": {"function", "arrow function", "=>"},
        "return_value": {"return"},
        "arithmetic_operation": set(),
        "string_operation": {"template literal", ".concat(", "${"},
        "type_conversion": {"parseint", "parsefloat", "number(", "string(", "tostring"},
        "input": {"prompt(", "readline"},
        "output": {"console.log"},
    },
}

# One hardcoded, category-neutral fallback question per category - used
# only if the LLM leaks vocabulary twice in a row for that category (see
# round3_construct_engine.leaking_categories).
FALLBACK_QUESTIONS = {
    "variable": "What information does your program need to keep track of here?",
    "collection": "How do you want to represent and hold onto that information in your program?",
    "element_access": "How should your program get to a specific piece of that information?",
    # Not "...one at a time?" - that phrase names the mechanism (sequential,
    # single-item handling) as surely as saying "loop" would. Asking about
    # the values as a set, with no hint of how they get visited, leaves
    # the technique entirely open.
    "iteration": "Once your program has all of the values, what needs to happen with them?",
    "nested_iteration": "After handling one of those, what else needs to be examined for it?",
    "condition": "What should determine whether this step happens or not?",
    # Not "...comparing those two values?" - "comparing" is a plain-English
    # synonym for the very thing being tested; it passes the literal
    # blocklist but still names the operation. Ask about the outcome
    # instead of the operation that produces it.
    "comparison": "What should determine which of the two values is the one to keep going forward?",
    # Not "...does everything need to hold, or just one part?" - that's
    # AND/OR presented as a two-item multiple choice in disguise. Ask
    # about the combined requirement without splitting it into options.
    "boolean_logic": "What has to be true, across all of those individual checks together, for the overall result to count?",
    # Not "...packaged so it can be used?" - "packaged...used" is a
    # synonym for defining a function. Ask about the dependency instead
    # of the mechanism that would satisfy it.
    "function": "How should the rest of the program be able to make use of this piece of logic?",
    # Not "...given in order to run?" - "given...to run" is a synonym for
    # parameters. Ask about the dependency, not the mechanism.
    "parameter": "What information does this piece of logic depend on from outside itself?",
    # Not "...hand back once it's done?" - "hand back" is a synonym for
    # return. Ask about what's needed afterward, not the mechanism that
    # supplies it.
    "return_value": "What does the rest of the program need from this piece of logic once it finishes?",
    "arithmetic_operation": "What calculation should be performed here?",
    "string_operation": "What should happen to combine or reshape that text?",
    "type_conversion": "What form does that value need to be in before it's used this way?",
    "input": "Where should this value come from?",
    "output": "What should the program show once this is done?",
}


def forbidden_vocab(category: str, language: str) -> set[str]:
    generic = GENERIC_VOCAB.get(category, set())
    lang_specific = LANGUAGE_CONSTRUCTS.get(language, {}).get(category, set())
    return {w.lower() for w in generic | lang_specific}


def contains_forbidden_vocab(text: str, category: str, language: str) -> bool:
    # Alphabetic words/phrases are matched at word boundaries, so "list"
    # doesn't falsely trip inside "arraylist" and "or" doesn't falsely
    # trip inside "before" - but still catches the word used on its own
    # ("a list of", "combine conditions"). Symbol tokens ("==", "+",
    # "&&") have no meaningful word boundary, so those fall back to
    # plain substring containment, which is exactly right for code-shaped
    # fragments like "int(" appearing inside a longer expression.
    lowered = text.lower()
    for word in forbidden_vocab(category, language):
        if all(ch.isalpha() or ch == " " for ch in word):
            if re.search(r"\b" + re.escape(word) + r"\b", lowered):
                return True
        elif word in lowered:
            return True
    return False
