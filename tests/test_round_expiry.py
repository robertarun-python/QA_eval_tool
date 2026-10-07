"""
The guaranteed way a round closes once its timer hits zero and a real
submit isn't possible (empty/incomplete content, or losing a race against
the server's own deadline check) - see routers/candidate.py's
POST /round/{n}/expire and app.js's forceExpireRound. Before this existed,
app.js's client-side "add a row first"-style validation ran unconditionally,
even on the timer's own auto-submit call, silently blocking it forever -
a candidate whose content was empty or incomplete when time ran out was
left staring at an expired timer with no way for the round to end.

/expire finalizes the round as a real submission (status "submitted",
scored like any other) rather than a separate terminal "expired" state -
whatever draft content existed (possibly none at all) is saved as-is and
the candidate moves on to the next round. A candidate who ran out of time
having written nothing still has to progress; "didn't attempt it" is a
scoring outcome, not grounds to strand the round.
"""
from datetime import datetime, timedelta

from .conftest import (
    HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)


def _expire_started_at(round_number, minutes_ago):
    import app.database as database_module
    from app.models import Submission

    db = database_module.SessionLocal()
    submission = db.query(Submission).filter(Submission.round_number == round_number).one()
    submission.started_at = datetime.utcnow() - timedelta(minutes=minutes_ago)
    db.commit()
    db.close()


def _stub_round1_scoring(monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {
            "coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0,
            "feedback_text": "No test cases were written before time ran out.",
            "concept_coverage": [],
            "_provenance": {"model": "claude-sonnet-4-5", "prompt_file": "round1_scoring.txt", "prompt_hash": "abc123"},
        },
    )


def test_expire_with_no_content_still_finalizes_and_scores_the_round(client, monkeypatch):
    """The exact bug report: a candidate who wrote zero test cases before
    the timer ran out must still move on, not get stuck on a dead-end
    screen."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Timed scenario")  # 30-minute default limit
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _expire_started_at(1, minutes_ago=31)

    res = client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token))
    assert res.status_code == 200
    # The response reflects the state right after finalizing, before the
    # background scoring task (scheduled by this same request) has run -
    # same as any other submit endpoint. See the follow-up GET below for
    # the scored state.
    assert res.json()["status"] == "submitted"
    assert res.json()["content"] == []

    state = client.get("/candidate/round/1", cookies=_auth(cand_token)).json()
    assert state["submission"]["status"] == "scored"
    assert state["submission"]["content"] == []

    # Reflected on HR's dashboard too - scored, not stuck.
    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    c1 = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in c1["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "scored"
    assert round1["final_score"] == 0


def test_expire_saves_whatever_draft_content_was_sent(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Timed scenario")
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _expire_started_at(1, minutes_ago=31)

    # A partial row - missing steps/expected_result, exactly what a real
    # submit would have rejected with a 422.
    res = client.post(
        "/candidate/round/1/expire",
        json={"content": [{"title": "Login with valid creds"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 200
    saved = res.json()["content"]
    assert saved == [{
        "title": "Login with valid creds", "preconditions": "", "steps": "", "test_data": "", "expected_result": "",
        "priority": "Medium", "type": "Positive",
    }]


def test_expire_moves_the_candidate_on_to_the_next_round(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1, title="Timed scenario")
    _publish_scenario(client, hr_token, monkeypatch, round_number=4, title="Debug scenario")
    _stub_round1_scoring(monkeypatch)
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    _expire_started_at(1, minutes_ago=31)
    client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token))

    # Round 1 is done - can't resubmit it (same as any other completed round).
    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 400

    # Round 2 unlocks, same as any other completed round.
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 200


def test_expire_rejects_a_round_whose_deadline_hasnt_passed_yet(client, monkeypatch):
    """The frontend only calls this after a real submit attempt already
    failed, but the server is still the final authority - this can't be
    used to skip a round early just by claiming time is up."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Timed scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))

    res = client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token))
    assert res.status_code == 400
    assert "hasn't passed yet" in res.json()["detail"]


def test_expire_requires_an_in_progress_submission(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Timed scenario")
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    # Never started.
    res = client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token))
    assert res.status_code == 400

    # Already submitted.
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    res = client.post("/candidate/round/1/expire", json={}, cookies=_auth(cand_token))
    assert res.status_code == 400


def test_expire_is_candidate_only(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.post("/candidate/round/1/expire", json={}, cookies=_auth(hr_token)).status_code == 403


def test_expire_rejects_invalid_round_number(client, monkeypatch):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.post("/candidate/round/9/expire", json={}, cookies=_auth(cand_token)).status_code == 400
