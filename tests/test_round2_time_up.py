"""
Round 2 (AI-assisted automation) running out of time.

The bug this guards: when the Round 2 clock hit zero, the page auto-submitted
with a field the endpoint doesn't accept (it was silently dropped), and when
that failed it expired ROUND 4 instead of round 2 - so the candidate's round 2
stayed open. The page side is read statically; the server side is driven
through the real expire endpoint. Offline: no model call.
"""
import re
from datetime import datetime, timedelta

from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario
from .page_js import page_js
from .test_round4_auto import _publish_auto_scenario, _reach_automation_round


def _function(js: str, name: str) -> str:
    start = js.index(f"async function {name}(")
    nxt = re.search(r"\n(async )?function ", js[start + 1:])
    return js[start:start + 1 + nxt.start()] if nxt else js[start:]


def test_time_up_submits_round2_and_falls_back_to_expiring_round2():
    body = _function(page_js(), "round4AutoSubmit")
    assert '"/candidate/round/2/auto/submit"' in body
    assert "JSON.stringify({ entries })" in body
    assert "validation" not in body  # the dropped field
    assert "forceExpireRound(2)" in body
    assert not re.search(r"forceExpireRound\((?!2\))", body)


def test_round2_timer_is_wired_to_the_round2_auto_submit():
    body = _function(page_js(), "renderRound4View")
    assert "startTimer(deadline, round4AutoSubmit, 2)" in body


def test_expire_closes_an_overdue_round2_attempt(client, monkeypatch):
    from app.services import llm_service
    monkeypatch.setattr(llm_service, "score_round4_auto_conversation", lambda **kwargs: {
        "scores": {}, "final_score": 0, "findings": [], "feedback_text": "Nothing submitted.",
    })
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, round_number=1)
    _publish_auto_scenario(client, hr_token, monkeypatch)
    cand_token = _reach_automation_round(client, hr_token, monkeypatch)

    import app.database as database_module
    from app.models import RoundStatus, Submission
    db = database_module.SessionLocal()
    submission = db.query(Submission).filter(Submission.round_number == 2).one()
    submission.started_at = datetime.utcnow() - timedelta(hours=3)
    db.commit()
    db.close()

    res = client.post("/candidate/round/2/expire", json={}, cookies=_auth(cand_token))
    assert res.status_code in (200, 201), res.text

    db = database_module.SessionLocal()
    status = db.query(Submission).filter(Submission.round_number == 2).one().status
    db.close()
    assert status in (RoundStatus.submitted, RoundStatus.scored)
