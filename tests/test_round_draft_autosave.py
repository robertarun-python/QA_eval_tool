"""
Rounds 1/2's periodic in-progress autosave (see candidate.py's PATCH
/round/{round_number}/draft) - the same pattern round 3's test cases
already have via PATCH /round/3/test-case/{id}/draft (see test_round3.py),
just for a whole-round form instead of a per-test-case composer. Without
this, a crash, refresh, or network loss mid-round silently lost whatever
the candidate had typed while their timer kept counting down - a real
gap in a tool whose whole value is a trustworthy score, since losing
work isn't the same as "didn't attempt it".
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def test_round1_draft_is_saved_and_returned_on_resume(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    draft = [{"title": "Login works", "preconditions": "", "steps": "typing...", "expected_result": ""}]
    res = client.patch("/candidate/round/1/draft", json={"content": draft}, cookies=_auth(cand_token))
    assert res.status_code == 204

    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["content"] == [
        {"title": "Login works", "preconditions": "", "steps": "typing...", "expected_result": "",
         "priority": "Medium", "type": "Positive"},
    ]
    # Still genuinely in progress - autosave never finalizes anything.
    assert state["submission"]["status"] == "in_progress"


def test_round2_draft_is_saved_and_returned_on_resume(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Debug scenario")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.patch(
        "/candidate/round/2/draft",
        json={"investigation": [{"area": "Checked the logs"}], "root_cause": "still investigating..."},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 204

    state = client.get("/candidate/round/2", cookies=_auth(cand_token)).json()
    assert state["submission"]["content"] == {
        "investigation": [{"area": "Checked the logs"}],
        "root_cause": "still investigating...",
    }
    assert state["submission"]["status"] == "in_progress"


def test_round2_draft_keeps_a_blank_investigation_row_unlike_a_real_submit(client, monkeypatch):
    """Unlike /expire and a real submit (which drop rows with no area
    filled in - a finalized answer should only keep what's genuinely
    there), autosave must reproduce the exact editing state, including a
    row the candidate hasn't finished typing into yet - dropping it on
    every autosave would make a half-typed row silently vanish out from
    under a candidate who's still mid-keystroke on the NEXT row."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_scenario(client, hr_token, monkeypatch, round_number=2, title="Debug scenario")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    client.post("/candidate/round/2/start", cookies=_auth(cand_token))

    res = client.patch(
        "/candidate/round/2/draft",
        json={"investigation": [{"area": "Checked the logs"}, {"area": ""}], "root_cause": ""},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 204

    state = client.get("/candidate/round/2", cookies=_auth(cand_token)).json()
    assert state["submission"]["content"]["investigation"] == [
        {"area": "Checked the logs"}, {"area": ""},
    ]


def test_draft_rejected_once_the_round_is_already_submitted(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    res = client.patch("/candidate/round/1/draft", json={"content": [{"title": "too late"}]}, cookies=_auth(cand_token))
    assert res.status_code == 400


def test_draft_rejected_before_the_round_has_been_started(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    res = client.patch("/candidate/round/1/draft", json={"content": [{"title": "x"}]}, cookies=_auth(cand_token))
    assert res.status_code == 400


def test_draft_rejects_round_3_which_autosaves_per_test_case_instead(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    res = client.patch("/candidate/round/3/draft", json={"content": []}, cookies=_auth(cand_token))
    assert res.status_code == 400


def test_a_real_submit_overrides_whatever_was_autosaved(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 80, "feedback_text": "ok"},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.patch("/candidate/round/1/draft", json={"content": [{"title": "stale draft"}]}, cookies=_auth(cand_token))

    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "final answer", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )

    hr_report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in hr_report if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["status"] in ("submitted", "scored")
