"""
Spend guard for the live replay tests (anything in tests/replay run with
RUN_LLM_REPLAY=1). Each live run calls the real Claude API on the paid
key; in Sep 2026 a development session re-ran them in a loop and drained
the API balance. So, unless REPLAY_FORCE=1 is set by a person:

- a live run is refused within MIN_MINUTES_BETWEEN_RUNS of the last one
- at most MAX_RUNS_PER_DAY live runs in 24 hours

Run history lives in pytest's own cache (.pytest_cache, not committed).
Develop against the normal suite, which never calls the API; run this
once at the end of a change.
"""
import os
import time

import pytest

MIN_MINUTES_BETWEEN_RUNS = 10
MAX_RUNS_PER_DAY = 3
_CACHE_KEY = "replay/live_runs"


def pytest_collection_modifyitems(session, config, items):
    live = [i for i in items if os.environ.get("RUN_LLM_REPLAY") == "1" and "tests/replay" in str(i.path).replace("\\", "/")]
    if not live:
        return
    now = time.time()
    runs = [t for t in (config.cache.get(_CACHE_KEY, []) or []) if now - t < 86400]
    if os.environ.get("REPLAY_FORCE") != "1":
        if runs and now - max(runs) < MIN_MINUTES_BETWEEN_RUNS * 60:
            minutes = int((now - max(runs)) // 60)
            pytest.exit(
                f"Live replay refused: the last live run was {minutes} min ago (minimum gap "
                f"{MIN_MINUTES_BETWEEN_RUNS} min) - each run spends real API credit. Develop against the "
                "normal test suite; set REPLAY_FORCE=1 only if a person has approved another run.",
                returncode=2,
            )
        if len(runs) >= MAX_RUNS_PER_DAY:
            pytest.exit(
                f"Live replay refused: {len(runs)} live runs in the last 24 hours (limit {MAX_RUNS_PER_DAY}) - "
                "each run spends real API credit. Set REPLAY_FORCE=1 only if a person has approved another run.",
                returncode=2,
            )
    if config.option.collectonly:
        return  # listing tests spends nothing - don't count it as a run
    config.cache.set(_CACHE_KEY, runs + [now])
    print(f"\nLive replay: {len(live)} tests will call the real API "
          f"(run {len(runs) + 1} of {MAX_RUNS_PER_DAY} allowed in 24h).")
