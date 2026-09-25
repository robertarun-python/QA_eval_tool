"""
Round 1's reference (the answer key, and what the Round 2 practice app is
built from) is capped at what a candidate could write by hand in the round:
about one case per 1.6 minutes. Asking for "roughly" that got 19-28 cases
for a 20-minute round.
"""
from app.services import llm_service


def _row(n, priority):
    return {"title": f"case {n}", "steps": "s", "expected_result": "e", "priority": priority, "type": "Positive"}


def test_the_limit_follows_the_round_time():
    assert llm_service.round1_reference_case_limit(20) == 12
    assert llm_service.round1_reference_case_limit(30) == 19
    assert llm_service.round1_reference_case_limit(5) == 5  # never fewer than 5


def test_the_ai_is_told_the_limit_and_extra_cases_are_dropped_lowest_priority_first(monkeypatch):
    prompts = []
    rows = [_row(i, "Low" if i % 3 == 0 else "High" if i % 3 == 1 else "Medium") for i in range(25)]
    monkeypatch.setattr(llm_service, "_call_claude_json", lambda prompt, **kw: prompts.append(prompt) or rows)

    kept = llm_service.generate_round1_reference("An app.", "0-7", 20)

    assert "AT MOST 12 test cases" in prompts[0]
    assert len(kept) == 12
    assert all(r["priority"] != "Low" for r in kept)  # 8 High + 8 Medium available: all High, then Medium
    assert sum(r["priority"] == "High" for r in kept) == 8
    assert [int(r["title"].split()[1]) for r in kept] == sorted(int(r["title"].split()[1]) for r in kept)  # original order


def test_a_reference_within_the_limit_is_untouched(monkeypatch):
    rows = [_row(i, "Low") for i in range(10)]
    monkeypatch.setattr(llm_service, "_call_claude_json", lambda prompt, **kw: rows)
    assert llm_service.generate_round1_reference("An app.", "0-7", 20) == rows
