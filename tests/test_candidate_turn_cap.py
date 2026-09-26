"""
A candidate-facing AI turn (Round 2 assistant, Round 3 coding assistant) has
an overall limit: CANDIDATE_TURN_SECONDS for everything the turn does,
retries included. Before, each call could take ~3 minutes and the SDK retried
it twice - up to ~10 minutes of the candidate's round clock.
"""
import time

import pytest

from app.services import llm_service


class _Client:
    def __init__(self):
        self.options = None

    def with_options(self, **options):
        self.options = options
        return self


@pytest.fixture
def client(monkeypatch):
    fake = _Client()
    monkeypatch.setattr(llm_service, "_get_client", lambda: fake)
    return fake


def test_inside_a_turn_there_are_no_sdk_retries_and_no_call_outlives_the_turn(client):
    @llm_service.candidate_turn
    def turn():
        return llm_service._client_and_timeout(llm_service._get_client(), 8192)
    used, timeout = turn()
    assert used.options == {"max_retries": 0}
    assert timeout <= llm_service.CANDIDATE_TURN_SECONDS


def test_a_turn_out_of_time_stops_before_another_call(client, monkeypatch):
    @llm_service.candidate_turn
    def turn():
        monkeypatch.setattr(llm_service.time, "monotonic", lambda: real() + llm_service.CANDIDATE_TURN_SECONDS)
        return llm_service._client_and_timeout(llm_service._get_client(), 1024)
    real = time.monotonic
    with pytest.raises(llm_service.LLMTurnTooSlow):
        turn()


def test_outside_a_turn_nothing_changes(client):
    """Scoring and HR's generation keep the normal retries and timeouts."""
    used, timeout = llm_service._client_and_timeout(llm_service._get_client(), 8192)
    assert used.options is None and timeout == llm_service._timeout_for(8192)


def test_the_candidate_facing_turns_are_the_capped_ones():
    for name in ("round2_automation_turn", "round2_automation_clarify", "round3_coding_turn", "round3_syntax_fix"):
        assert getattr(llm_service, name).__wrapped__, name  # functools.wraps from candidate_turn
