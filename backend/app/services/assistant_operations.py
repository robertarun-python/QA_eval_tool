"""
The operations a candidate can state in their own words - one fixed, deterministic vocabulary
shared by Round 3's whole-task check (round3_policy) and scope check (round3_scope_guard).

Production, 2026-10-07 (candidate 4, Round 3): plain-English steps ("1. pick the unique elements
2. sort them in descending order 3. return the integer in the 1st index") were refused or
rejected because the rules only recognised programming words ("set", "descending", "print").
Each family below lists the everyday ways people state one operation, so the same intent gets
the same treatment whatever words it uses.

Families are verb phrases on purpose: "remove the duplicates" states an operation, "the second
largest distinct value" only restates the goal - a requirement, not a way of meeting it.
No model call, no similarity score: a message either names an operation or it doesn't.
"""
import re

_STEP_START = r"(?:^|\n)\s*(?:\d+\s*[.):-]|step\s*\d+\s*[.):-]?|[-*])\s*"

# Operations that transform the data - stating one is part of an approach.
TRANSFORM_FAMILIES = {
    "de-duplication": re.compile(
        r"\b(remove[sd]?|removing|drop(s|ped|ping)?|delet\w*|eliminat\w*|filter\w*\s+out|get(ting)?\s+rid\s+of|discard\w*|toss\w*|"
        r"exclud\w*|skip\w*|ignor\w*)\s+(all\s+)?(the\s+|any\s+)?(duplicat\w*|repeat\w*|dupes?|copies)\b"
        r"|\bde-?dup\w*"
        r"|\b(keep|keeps|keeping|pick|picks|picking|take|takes|taking|get|gets|getting|select\w*|extract\w*|retain\w*|collect\w*|"
        r"store\w*|make|put|puts|putting|place\w*|add|adds|adding|list|lists|listing|gather\w*|save\w*|copy|copies|copying|"
        r"move\w*|use|uses|using)\s+(only\s+)?(the\s+|all\s+(the\s+)?)?(unique|distinct)\b"
        r"|\bone\s+copy\b|\bonly\s+once\b"
        # gate case 5 (2026-10-07): "keep each value once" - each value kept (or appearing) a single time
        r"|\b(each|every)\s+(value|number|element|item|integer|entry)\s+(\w+\s+){0,3}?(only\s+)?(once|one\s+time|a\s+single\s+time)\b"
        r"|\bno\s+(value|number|element|item|integer|entry)\s+(\w+\s+){0,2}?(twice|more\s+than\s+once)\b"
        r"|\bwithout\s+(any\s+)?(duplicat\w*|repeat\w*|dupes?)\b"
        r"|\b(convert\w*|turn\w*|put\w*|change\w*)\s+(it|them|\w+)?\s*(in)?to\s+a\s+set\b|\buse\s+a\s+(hash\s*)?set\b"
        + "|" + _STEP_START + r"(the\s+)?(unique|distinct)\b",
        re.I),
    "sorting": re.compile(r"\b(sort\w*|arrang\w*|re-?order\w*|rank\w*|revers\w*)\b|\border\s+(them|it|the|by|from|in)\b", re.I),
    "a loop": re.compile(r"\b(iterat\w*|loop\w*|go(es|ing)?\s+through|travers\w*|for\s+(each|every)|scan\w*|walk\w*\s+through|one\s+by\s+one)\b", re.I),
    "storing": re.compile(
        r"\b(store\w*|append\w*|push\w*|insert\w*|keep\s+track)\b"
        r"|\b(create|make|build|declare)\w*\s+(a|an|the)\s+(new\s+)?(resultant\s+)?(list|array|map|dict\w*|set|result\w*)\b", re.I),
    "splitting/converting": re.compile(r"\b(split\w*|pars\w*|convert\w*|cast\w*|tokeni\w*)\b|\bcomma[- ]separated\b", re.I),
    "comparing/tracking": re.compile(r"\b(compar\w*|track\w*|swap\w*|updat\w*|initiali[sz]\w*)\b|\b(max|min)\s*\(", re.I),
    "counting": re.compile(r"\b(count\w*|frequenc\w*|tally\w*)\b", re.I),
    "indexing": re.compile(
        r"\b(index|position)\s*-?\d|\[\s*-?\d+\s*\]|\belement\s+at\b|\bindex\s+(0|1|zero|one)\b"
        r"|\b(first|second|1st|2nd|0th|last)\s+(element|item|index|position|value|entry|one)\b", re.I),
}

# Operations every program has - they count toward an approach only alongside a transformation.
OTHER_FAMILIES = {
    "reading input": re.compile(r"\bread\w*\b|\binput\b|\bstdin\b|\bgiven\s+an?\s+(array|list)\b", re.I),
    "output": re.compile(r"\b(print\w*|output\w*|display\w*|return\w*|show\w*)\b|\bgive\s+back\b", re.I),
}

# Wording equivalences the scope check accepts as the candidate naming that choice themselves.
DESCENDING = re.compile(
    r"\bdesc\b|descend|decreas|revers|high(est)?\s+to\s+low|max(imum)?\s+first"
    r"|(large|big|high)(st|r|er|est|gest|ger)?\s+(to|first)", re.I)
OUTPUT = OTHER_FAMILIES["output"]


def families(text: str | None) -> set[str]:
    """Every operation family the text states."""
    t = text or ""
    return {n for n, r in {**TRANSFORM_FAMILIES, **OTHER_FAMILIES}.items() if r.search(t)}


def is_candidate_approach(text: str | None) -> bool:
    """True when the text states an approach of the candidate's own: at least one data
    transformation plus at least one other operation (two families in all). A goal on its
    own ("find the second largest distinct value and print it") states no transformation."""
    found = families(text)
    return bool(found & set(TRANSFORM_FAMILIES)) and len(found) >= 2


def states_deduplication(text: str | None) -> bool:
    return bool(TRANSFORM_FAMILIES["de-duplication"].search(text or ""))
