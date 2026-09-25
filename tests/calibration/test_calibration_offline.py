"""The calibration set and its checks, offline (scripted model, no API):
every answer goes through the real scoring functions, and the checks fail a
scorer that can't tell strong from weak. The live run is run_calibration.py."""
from app.config import settings

from . import run_calibration as cal


def test_every_answer_goes_through_the_real_scorers(monkeypatch):
    monkeypatch.setattr(settings, "llm_fake_mode", True)  # scripted replies (services/fake_llm.py)
    out = cal.run(runs=1)
    for rnd, tiers in out["scores"].items():
        for tier, scores in tiers.items():
            assert all(isinstance(s, (int, float)) for s in scores), (rnd, tier, scores)


def test_checks_fail_a_scorer_that_gives_everyone_the_same_score(monkeypatch):
    monkeypatch.setattr(cal, "score", lambda rnd, tier: 72)
    failures = cal.run(runs=2)["failures"]
    assert any("ranking not clear" in f for f in failures)
    assert any("weak answer scored 72" in f for f in failures)


def test_checks_fail_an_inconsistent_scorer(monkeypatch):
    seq = iter([90, 60, 55, 55, 20, 20] * 4)
    monkeypatch.setattr(cal, "score", lambda rnd, tier: next(seq))
    assert any("spread over" in f for f in cal.run(runs=2)["failures"])


def test_checks_pass_a_scorer_that_ranks_correctly(monkeypatch):
    monkeypatch.setattr(cal, "score", lambda rnd, tier: {"strong": 85, "average": 60, "weak": 30}[tier])
    assert cal.run(runs=2)["failures"] == []
