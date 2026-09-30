"""
The per-candidate round limit counts one attempt - one submission - not every attempt the candidate ever
made at that round (live, 2026-09-30: after an HR reset, candidate4's earlier Round 2 attempts had already
used $0.7509 of the $0.75 and 59 of the 60 calls, so the first message of the new attempt would have been
blocked). The fake AI charges $0.30 a call; no real API call is made.
"""
import random

import pytest

from app.services import llm_service
from .test_ai_guardrails import make_paid

OLD = dict(round_number=2, scenario_id=1, submission_id=208, user_id=8)   # archived by the reset
NEW = {**OLD, "submission_id": 215}                                     # the attempt after it


@pytest.fixture
def paid(tmp_path, monkeypatch):
    return make_paid(tmp_path, monkeypatch)


def test_a_new_attempt_starts_with_a_fresh_allowance_and_the_old_one_stays_stopped(paid):
    paid.spent_before(0.7509, calls=59, **OLD)
    with llm_service.call_context(**OLD), pytest.raises(llm_service.AILimitReached) as stop:
        llm_service._call_claude("p")                        # e.g. a retried scoring of the old attempt
    assert stop.value.limit == "candidate_round_usd" and paid.sent == []
    with llm_service.call_context(**NEW):
        llm_service._call_claude("p")                        # 0.30
        llm_service._call_claude("p")                        # 0.60
        llm_service._call_claude("p")                        # 0.90 - this attempt's own limit now reached
        with pytest.raises(llm_service.AILimitReached):
            llm_service._call_claude("p")
    assert len(paid.sent) == 3
    assert llm_service._spend_now()["candidates"][(8, 2, 215)] == [pytest.approx(0.90), 3]


def test_the_call_count_is_per_attempt_too(paid):
    paid.script["tokens"] = 1
    paid.spent_before(0.01, calls=60, **OLD)
    with llm_service.call_context(**OLD), pytest.raises(llm_service.AILimitReached) as stop:
        llm_service._call_claude("p")
    assert stop.value.limit == "candidate_round_calls"
    with llm_service.call_context(**NEW):
        llm_service._call_claude("p")
    assert len(paid.sent) == 1


def test_the_daily_and_monthly_limits_still_add_up_every_attempt(paid):
    """A new attempt gets a fresh round allowance - never a fresh day or month."""
    paid.spent_before(3.31, **OLD)                            # over today's $3 + the candidate's 10% grace, all on the old attempt
    with llm_service.call_context(**NEW), pytest.raises(llm_service.AILimitReached) as stop:
        llm_service._call_claude("p")
    assert stop.value.limit == "daily" and paid.sent == []
    llm_service.settings.ai_daily_limit_usd = 1000.0
    llm_service._MONTH_SPEND.update(month=None, usd=0.0)
    paid.spent_before(10.0, **OLD)                            # the month: $12 + 10% = $13.20 reached
    with llm_service.call_context(**NEW), pytest.raises(llm_service.AIBudgetReached):
        llm_service._call_claude("p")
    assert paid.sent == []


@pytest.mark.parametrize("seed", range(5))
def test_random_sequences_match_a_simple_model(paid, seed):
    """Random calls across candidates, rounds and attempts: a call goes out exactly when its own attempt is
    under both limits (the day/month limits are set high enough not to interfere)."""
    llm_service.settings.ai_daily_limit_usd = 1000.0
    llm_service.settings.ai_monthly_limit_usd = 1000.0
    llm_service.settings.ai_candidate_round_calls = 4
    rng = random.Random(seed)
    model: dict = {}
    attempts = [(u, r, s) for u in (8, 9) for r in (1, 2) for s in (1, 2)]
    for _ in range(60):
        u, r, s = rng.choice(attempts)
        usd, calls = model.get((u, r, s), (0.0, 0))
        should_send = usd < 0.75 - 1e-9 and calls < 4
        before = len(paid.sent)
        with llm_service.call_context(user_id=u, round_number=r, submission_id=s * 100 + u):
            try:
                llm_service._call_claude("p")
            except llm_service.AILimitReached:
                pass
        sent = len(paid.sent) > before
        assert sent == should_send, ((u, r, s), usd, calls)
        if sent:
            model[(u, r, s)] = (usd + 0.30, calls + 1)
