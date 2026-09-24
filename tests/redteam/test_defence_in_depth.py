"""
Every cheating attempt in attacks.py, against a model that always gives in
(see harness.py): none may reach the candidate. Offline - nothing calls the API.
Baseline when written (Sep 2026): R3 8 blocked before the model, 22 stopped
after it; R2 9 blocked, 5 stopped (12 of 14 leaked before invented_content).
"""
import pytest

from . import attacks, harness


@pytest.mark.parametrize("category, prompt", attacks.R3, ids=[f"R3-{c}-{i}" for i, (c, _) in enumerate(attacks.R3)])
def test_round3_attempt_never_reaches_the_candidate(monkeypatch, category, prompt):
    outcome, result = harness.run_r3(monkeypatch, prompt)
    assert outcome != "leaked", (category, prompt, result)


@pytest.mark.parametrize("category, prompt", attacks.R2, ids=[f"R2-{c}-{i}" for i, (c, _) in enumerate(attacks.R2)])
def test_round2_attempt_never_reaches_the_candidate(monkeypatch, category, prompt):
    outcome, result = harness.run_r2(monkeypatch, prompt)
    assert outcome != "leaked", (category, prompt, result)
