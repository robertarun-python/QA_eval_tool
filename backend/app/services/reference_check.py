"""
Round 1 test cases that contradict each other - found when a scenario is
reviewed, before any build (no AI call). Measured case (2026-09-27): Loan TC-2
said the loan outstanding drops to 1,19,750 after a 5,250 EMI, TC-10 that it
drops to 1,21,200 (only the principal comes off); both were in the published
answer key, so a candidate writing the right figure could lose marks.

Two test cases are flagged when they start from a shared amount (the same EMI,
balance or price), their expected results describe the same thing (a shared
word next to the amount, e.g. "outstanding") and the amounts are close but
different (within 3%) - the signature of one rule computed two ways.
"""
import re

from .practice_app.generator import NEAR, _amounts

_WORD_RE = re.compile(r"[a-z]{3,}")
_NOT_A_LABEL = {"the", "and", "with", "after", "before", "from", "shows", "show", "shown", "displayed", "display", "should", "must",
                "will", "updated", "update", "new", "total", "amount", "value", "equals", "equal", "reduced", "increased", "becomes",
                "rs", "inr", "usd", "only", "not", "then", "for", "per", "each", "page", "message", "successful", "success"}
_AMOUNT_WITH_TEXT_RE = re.compile(r"(?<![\w.@#$&*-])(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w@#$&*-])")


def _labelled(text: str) -> list[tuple[float, set[str]]]:
    """(amount, the words just before it) for each amount of 10 or more in a text."""
    out = []
    kept = set(_amounts(text))
    for m in _AMOUNT_WITH_TEXT_RE.finditer(text or ""):
        value = float(m.group(1).replace(",", ""))
        if value not in kept:
            continue  # a date, time or id digit - _amounts leaves those out
        before = _WORD_RE.findall(text[max(0, m.start() - 40):m.start()].lower())[-4:]
        out.append((value, {w for w in before if w not in _NOT_A_LABEL}))
    return out


def _inputs(case: dict) -> set[float]:
    return set(_amounts(" ".join(str(case.get(k) or "") for k in ("preconditions", "steps", "test_data"))))


def contradictions(cases: list) -> list[str]:
    """Plain sentences for HR, one per pair of test cases that seem to contradict each other."""
    rows = [c for c in cases or [] if isinstance(c, dict)]
    out = []
    for i, a in enumerate(rows):
        for j in range(i + 1, len(rows)):
            b = rows[j]
            if not _inputs(a) & _inputs(b):
                continue
            b_results = _labelled(str(b.get("expected_result") or ""))
            b_all = {v for v, _ in b_results} | _inputs(b)
            a_all = {v for v, _ in _labelled(str(a.get("expected_result") or ""))} | _inputs(a)
            for x, x_words in _labelled(str(a.get("expected_result") or "")):
                if x in b_all:
                    continue
                for y, y_words in b_results:
                    if y in a_all or not (x_words & y_words) or not 0 < abs(x - y) <= NEAR * max(x, y):
                        continue
                    what = sorted(x_words & y_words)[0]
                    out.append(f'"{a.get("title", f"test case {i + 1}")}" expects {what} {x:,.2f}, but '
                               f'"{b.get("title", f"test case {j + 1}")}" expects {y:,.2f} from the same starting amount - '
                               "check which one is right before candidates are scored against them")
                    break
    return list(dict.fromkeys(out))
