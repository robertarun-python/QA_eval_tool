"""
Round 3: AI-prompted coding - see
docs/superpowers/specs/2026-08-23-round3-ai-coding-design.md. Not to be
confused with tests/test_round4.py (the renamed automation round).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth, _publish_scenario


def test_hr_can_create_and_publish_a_round3_coding_scenario(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, round_number=3, title="Add two numbers")
    assert scenario["round_number"] == 3

    # _publish_scenario returns the pre-publish creation response, not a
    # re-fetch (see test_round1.py's edit_time_limit_after_publish test) -
    # confirm the actual current state via a fresh read rather than
    # trusting that stale dict for status.
    fetched = client.get(f"/hr/scenarios/{scenario['id']}", cookies=_auth(hr_token)).json()
    assert fetched["status"] == "published"
    assert fetched["reference_json"]["test_cases"][0]["expected_output"] == "5"
    assert "expected_approach" in fetched["reference_json"]
