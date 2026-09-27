"""
The candidate's journey, Round 1 -> Round 2 on a real practice app, as a state x
event matrix: random sequences of what really happens - a second login
elsewhere, an old tab still sending requests, refresh, logout from the old or
the current session, the timer running out, HR resetting the candidate - with
the journey's promises checked after EVERY event. Built after the owner lost a
Round 1 twice to an old session's automatic logout (2026-09-27): three rules,
each tested alone, broke together. The AI and the test Run are stubbed - no
cost; fixed seeds, so a failure always reproduces.

Promises:
  P1  no event is answered with a server error
  P2  a round in progress ends only by the candidate's own submit, a logout
      from the candidate's CURRENT session, its timer, or an HR reset
  P3  work saved in a round in progress is never lost on refresh
  P4  an old (replaced) session never changes anything
  P5  after an HR reset the candidate has no attempts and can start Round 1
  P6  Round 2 can't start before Round 1 is finished; a finished round stays finished
"""
import io
import random
from datetime import datetime, timedelta

import pytest

from app import database
from app.models import RoundStatus, Submission, User
from app.services import execution_service, llm_service
from app.services.practice_app import service
from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login
from .test_practice_app_service import _leave_round1, _recorded_leave

DONE = {RoundStatus.submitted, RoundStatus.scored}
COVERAGE: dict = {}  # (round 1 state, round 2 state, logged in, event) -> times run


@pytest.fixture
def journey(client, monkeypatch, tmp_path):
    """A live Round 1 whose Round 2 runs on a real (engine-built) practice app."""
    monkeypatch.setattr(service, "BUILDS_DIR", tmp_path)
    recorded, titles = _recorded_leave()
    r1 = _leave_round1(client, monkeypatch, titles)
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("the journey made a real AI call"))
    service.install_recorded(r1["id"], recorded)
    res = client.post(f"/hr/scenarios/{r1['id']}/practice-app/approve", cookies=_auth(hr))
    assert res.status_code == 200, res.text
    score = {"coverage_score": 50, "misses": [], "final_score": 50, "feedback_text": "ok"}
    monkeypatch.setattr(llm_service, "score_round1_submission", lambda **k: dict(score))
    monkeypatch.setattr(llm_service, "score_round2_automation_conversation", lambda **k: {**score, "findings": []})
    monkeypatch.setattr(llm_service, "_call_claude_json",
                        lambda *a, **k: {"reply": "Here's the code for what you described.", "code": 'incomplete("step one")'})
    ok = execution_service.ExecutionResult(stdout="PASS", stderr="", exit_code=0, timed_out=False, infra_error=False, duration_ms=1)
    from app.services.practice_engine import practice_run
    monkeypatch.setattr(practice_run, "run", lambda *a, **k: ok)
    monkeypatch.setattr(execution_service, "run_code", lambda **k: ok)
    return hr


def _db_rounds(email):
    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).one()
        subs = db.query(Submission).filter(Submission.user_id == user.id, Submission.archived.is_(False)).all()
        out = {}
        for s in subs:
            out[s.round_number] = ("done" if s.status in DONE else s.status.value, s.content)
        return out
    finally:
        db.close()


def _age(round_number, minutes):
    """The timer: this round started `minutes` ago."""
    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.email == CANDIDATE1_EMAIL).one()
        for s in db.query(Submission).filter(Submission.user_id == user.id, Submission.archived.is_(False),
                                             Submission.round_number == round_number).all():
            s.started_at = datetime.utcnow() - timedelta(minutes=minutes)
        db.commit()
    finally:
        db.close()


ROW = {"title": "Apply casual leave", "preconditions": "Signed in", "steps": "1. Apply 2. Check", "test_data": "2 days",
       "expected_result": "Leave applied", "priority": "High", "type": "Positive"}


class Journey:
    def __init__(self, client, hr, rng):
        self.c, self.hr, self.rng = client, hr, rng
        self.sessions = []          # tokens, newest last; only the newest is current
        self.current = None         # None when the current session logged out
        self.log = []

    def call(self, method, path, token, **kw):
        self.c.cookies.clear()
        res = getattr(self.c, method)(path, cookies=_auth(token) if token else None, **kw)
        assert res.status_code < 500, f"P1 server error on {method.upper()} {path}: {res.status_code} {res.text[:300]}\n{self.log}"
        return res

    def check(self, before, after, event, expect):
        """expect: {round: allowed end states} for this event; anything else must not change."""
        for rnd in (1, 2):
            b, a = before.get(rnd), after.get(rnd)
            if b == a:
                continue
            b_state, a_state = (b or ("none", None))[0], (a or ("none", None))[0]
            if b_state == a_state == "in_progress":
                continue  # content saved or edited - checked separately
            allowed = expect.get(rnd, set())
            assert (b_state, a_state) in allowed, (
                f"P2/P4/P6 round {rnd} went {b_state} -> {a_state} on '{event}'\n" + "\n".join(self.log[-15:]))

    def step(self):
        rng, before = self.rng, _db_rounds(CANDIDATE1_EMAIL)
        r1 = (before.get(1) or ("none",))[0]
        r2 = (before.get(2) or ("none",))[0]
        events = ["login", "refresh", "old_request", "old_logout", "logout", "r1_start", "r1_draft", "r1_submit",
                  "r2_start", "r2_work", "r2_submit", "timer_ends", "hr_reset"]
        event = rng.choice(events)
        self.log.append(f"{event} (r1={r1}, r2={r2}, logged_in={self.current is not None}, sessions={len(self.sessions)})")
        tok, expect = self.current, {}
        cell = (r1, r2, self.current is not None, event)
        COVERAGE[cell] = COVERAGE.get(cell, 0) + 1
        old = [t for t in self.sessions if t != self.current]

        if event == "login":
            self.current = _login(self.c, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
            self.sessions.append(self.current)
        elif event == "refresh":
            if tok:
                state = self.call("get", "/candidate/round/1", tok)
                if r1 == "in_progress" and before[1][1] and state.status_code == 200:
                    got = (state.json().get("submission") or {}).get("content")
                    assert got == before[1][1], f"P3 saved Round 1 work lost on refresh: {got!r}\n{self.log[-10:]}"
                if r1 == "done":
                    self.call("get", "/candidate/round/2/auto/state", tok)
        elif event == "old_request" and old:
            t = rng.choice(old)
            self.call("patch", "/candidate/round/1/draft", t, json={"content": [dict(ROW, title="from an old tab")]})
            self.call("post", "/candidate/round/1/submit", t, json={"content": [ROW]})
        elif event == "old_logout" and old:
            self.call("post", "/auth/logout", rng.choice(old))
        elif event == "logout" and tok:
            self.call("post", "/auth/logout", tok)
            self.current = None
            expect = {1: {("in_progress", "done")}, 2: {("in_progress", "done")}}
        elif event == "r1_start" and tok:
            self.call("post", "/candidate/round/1/start", tok)
            expect = {1: {("none", "in_progress")}}
        elif event == "r1_draft" and tok:
            self.call("patch", "/candidate/round/1/draft", tok, json={"content": [dict(ROW, title=f"draft {rng.random():.6f}")]})
        elif event == "r1_submit" and tok:
            self.call("post", "/candidate/round/1/submit", tok, json={"content": [ROW]})
            expect = {1: {("in_progress", "done")}}
        elif event == "r2_start" and tok:
            res = self.call("post", "/candidate/round/2/start", tok)
            if r1 != "done":
                assert res.status_code >= 400, f"P6 Round 2 started before Round 1 finished\n{self.log[-10:]}"
            expect = {2: {("none", "in_progress")}}
        elif event == "r2_work" and tok and r2 == "in_progress":
            self.call("post", "/candidate/round/2/auto/language", tok, json={"language": rng.choice(["java", "python", "javascript"])})
            self.call("post", "/candidate/round/2/auto/select", tok, json={"row_indexes": [0]})
            self.call("post", "/candidate/round/2/auto/turn", tok, json={"candidate_prompt": "Apply 2 days of casual leave. Generate the code."})
            self.call("post", "/candidate/round/2/auto/run", tok, json={})
        elif event == "r2_submit" and tok:
            self.call("post", "/candidate/round/2/auto/submit", tok, json={"code": 'incomplete("step one")'})
            expect = {2: {("in_progress", "done")}}
        elif event == "timer_ends":
            rnd = 1 if r1 == "in_progress" else 2 if r2 == "in_progress" else None
            if rnd:
                _age(rnd, 24 * 60)
                if tok:
                    self.call("get", f"/candidate/round/{rnd}", tok)
                expect = {rnd: {("in_progress", "done"), ("in_progress", "in_progress")}}  # closed on the next look
        elif event == "hr_reset":
            f = io.BytesIO(f"email,exam_date\n{CANDIDATE1_EMAIL},{datetime.utcnow().date()}\n".encode())
            self.call("post", "/hr/candidates/upload", self.hr, files={"file": ("reset.txt", f, "text/plain")})
            after = _db_rounds(CANDIDATE1_EMAIL)
            assert after == {}, f"P5 attempts left after an HR reset: {after}\n{self.log[-10:]}"
            return
        self.check(before, _db_rounds(CANDIDATE1_EMAIL), event, expect)


@pytest.mark.parametrize("seed", range(20))
def test_the_candidate_journey_keeps_its_promises(client, journey, seed):
    j = Journey(client, journey, random.Random(seed))
    for _ in range(60):
        j.step()


def test_the_random_journeys_reached_the_states_that_matter():
    """Passing means little if the journeys never got deep: every state that
    matters must have seen the disruptive events. Runs after the journeys above."""
    if not COVERAGE:
        pytest.skip("run together with the journeys above")
    must = {("in_progress", "none"): {"login", "old_request", "old_logout", "logout", "refresh", "timer_ends", "hr_reset", "r1_submit"},
            ("done", "in_progress"): {"login", "old_request", "old_logout", "logout", "refresh", "timer_ends", "hr_reset", "r2_work", "r2_submit"}}
    missing = {state: sorted(events - {ev for (r1, r2, _, ev) in COVERAGE if (r1, r2) == state}) for state, events in must.items()}
    print("state x event cells run:", len(COVERAGE), "| events run:", sum(COVERAGE.values()))
    assert not any(missing.values()), f"never reached: {missing}"
