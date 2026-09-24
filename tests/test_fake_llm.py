"""
Fake-AI mode (settings.llm_fake_mode / services/fake_llm.py): every scripted
reply goes through the REAL call-site function and must meet the same
contract as a real reply (the CALLS table from test_llm_bad_replies.py), the
API is never touched, and the mode is impossible to miss. Plus one API-level
walkthrough of all four rounds in fake mode with nothing else mocked - the
path the browser tests and manual walkthroughs rely on.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import pytest

from app.config import settings
from app.services import llm_service

from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _login, _auth
from .test_llm_bad_replies import CALLS


@pytest.fixture()
def fake_mode(monkeypatch):
    monkeypatch.setattr(settings, "llm_fake_mode", True)

    def no_api():
        raise AssertionError("fake AI mode must never reach the API")

    monkeypatch.setattr(llm_service, "_get_client", no_api)


SCRIPTED = sorted(set(CALLS) - {"score_round4_conversation", "score_round4_pilot_conversation", "round4_respond", "_round4_force_flaw"})


@pytest.mark.parametrize("call_name", SCRIPTED)
def test_scripted_reply_meets_the_real_call_sites_contract(fake_mode, call_name):
    fn, kwargs, contract = CALLS[call_name]
    assert contract(fn(**kwargs)), call_name


def test_code_turns_keep_the_code_and_add_only_a_comment(fake_mode):
    code = "nums = [1, 2]\nprint(nums)\n"
    r = llm_service.round3_coding_turn(scenario_description="d", language="python", conversation_so_far=[], current_code=code,
                                       candidate_prompt="Sort nums from largest to smallest.", turn_number=2)
    assert r["response_kind"] == "code_edit"  # passed the scope check, not the generic fallback
    assert r["code_after"].startswith(code) and r["code_after"].rstrip().endswith("Sort nums from largest to smallest.")


def test_syntax_fix_returns_the_candidates_code_unchanged(fake_mode):
    code = "total = 0\nfor n in [1, 2]:\n    total += n\nprint(total)\n"
    assert llm_service.round3_syntax_fix(code=code, language="python")["code_after"] == code


def test_calls_are_logged_as_fake(fake_mode):
    llm_service._CALL_LOG.clear()
    llm_service.score_round3_coding(scenario_description="d", expected_approach="e", conversation_so_far=[], test_results=[])
    assert llm_service.recent_calls()[0]["outcome"] == "fake"


def test_banner_and_health_show_fake_mode(client, fake_mode):
    assert "FAKE AI MODE" in client.get("/").text
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.get("/hr/ai-health", cookies=_auth(hr_token)).json()["mode"] == "fake"


def test_no_banner_when_off(client):
    assert settings.llm_fake_mode is False
    assert "FAKE AI MODE" not in client.get("/").text


def test_all_four_rounds_in_fake_mode_with_nothing_else_mocked(client, fake_mode, monkeypatch):
    """HR creates and publishes every round (answer keys from the fake AI),
    the candidate goes through R1 -> R2 automation -> R3 -> R4, and every
    round ends up scored. Only execution of Round 2's automation is local
    Python, as in real use."""
    from .test_round4_auto import PYTHON_ENV, GROUND_TRUTH
    hr = _login(client, HR_EMAIL, HR_PASSWORD)

    def publish(round_number, title, config=None):
        s = client.post("/hr/scenarios", json={"round_number": round_number, "title": title, "description": "Fake mode walkthrough.",
                                               "experience_band": "0-7", "time_limit_minutes": 20, "config_json": config or {}},
                        cookies=_auth(hr)).json()
        if round_number == 2:
            import app.database as database_module
            from app.models import Scenario
            db = database_module.SessionLocal()
            db.get(Scenario, s["id"]).reference_json = {"ground_truth": GROUND_TRUTH, "validation_notes": "n"}
            db.commit()
            db.close()
        res = client.post(f"/hr/scenarios/{s['id']}/publish", cookies=_auth(hr))
        assert res.status_code == 200, res.text

    publish(1, "R1")
    publish(2, "R2", {"mode": "ai_test_automation", "environment_code_by_language": {"python": PYTHON_ENV}})
    publish(3, "R3")
    publish(4, "R4")

    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    c = _auth(cand)
    assert client.post("/candidate/round/1/start", cookies=c).status_code == 201
    r = client.post("/candidate/round/1/submit", json={"content": [{"title": "Login", "steps": "1. log in", "test_data": "u/p",
                                                                     "expected_result": "Dashboard shows"}]}, cookies=c)
    assert r.status_code == 201, r.text

    assert client.post("/candidate/round/2/start", cookies=c).status_code == 201
    assert client.post("/candidate/round/2/auto/language", json={"language": "python"}, cookies=c).status_code == 201
    assert client.post("/candidate/round/2/auto/select", json={"row_indexes": [0]}, cookies=c).status_code == 201
    turn = client.post("/candidate/round/2/auto/turn", json={"candidate_prompt": "Encode step 1 with UI.login", "row_index": 0}, cookies=c)
    assert turn.status_code == 201 and turn.json()["response_kind"] == "code_edit", turn.text
    code = next(t["code"] for t in client.get("/candidate/round/2/auto/state", cookies=c).json()["tc_state"] if t["row_index"] == 0)
    run = client.post("/candidate/round/2/auto/run", json={"code": code, "row_index": 0}, cookies=c)
    assert run.status_code == 201, run.text
    sub = client.post("/candidate/round/2/auto/submit", json={"entries": [{"row_index": 0, "code": code}]}, cookies=c)
    assert sub.status_code == 201, sub.text

    assert client.post("/candidate/round/3/start", json={}, cookies=c).status_code == 201
    t3 = client.post("/candidate/round/3/turn", json={"candidate_prompt": "Read one line of input into nums."}, cookies=c)
    assert t3.status_code == 201 and t3.json()["response_kind"] == "code_edit", t3.text
    assert client.post("/candidate/round/3/submit", cookies=c).status_code == 201

    assert client.post("/candidate/round/4/start", cookies=c).status_code == 201
    r4 = client.post("/candidate/round/4/submit", json={"investigation": [{"area": "Checked logs"}], "root_cause": "Timeout"}, cookies=c)
    assert r4.status_code == 201, r4.text

    statuses = {s["round_number"]: s["status"] for s in client.get("/candidate/submissions", cookies=c).json()}
    assert statuses == {1: "scored", 2: "scored", 3: "scored", 4: "scored"}, statuses
