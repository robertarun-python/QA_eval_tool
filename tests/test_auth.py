from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _login, _auth, _publish_scenario


def test_seeded_hr_can_log_in(client):
    res = client.post("/auth/login", json={"identifier": HR_EMAIL, "password": HR_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "hr"
    # The token itself never appears in the JSON body - only as an
    # httpOnly Set-Cookie (see routers/auth.py). res.json() intentionally
    # has no access_token key to assert on anymore.
    assert "qa_eval_token" in res.cookies


def test_seeded_candidate_can_log_in(client):
    res = client.post("/auth/login", json={"identifier": CANDIDATE1_EMAIL, "password": CANDIDATE1_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "candidate"


def test_wrong_password_rejected(client):
    res = client.post("/auth/login", json={"identifier": CANDIDATE1_EMAIL, "password": "definitely-wrong"})
    assert res.status_code == 401


def test_unknown_email_rejected(client):
    res = client.post("/auth/login", json={"identifier": "nobody@example.com", "password": "whatever"})
    assert res.status_code == 401


def test_no_signup_route(client):
    # Accounts are seeded (app/seed.py) or bulk-uploaded by HR - there is
    # no public signup endpoint.
    res = client.post("/auth/signup", json={"email": "x@example.com", "password": "y", "role": "candidate"})
    assert res.status_code == 404


def test_hr_only_endpoint_rejects_candidate(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "x", "description": "y", "experience_band": "0-7"},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 403


def test_no_cookie_is_rejected(client):
    # No login at all - a direct call with nothing in the cookie jar.
    res = client.get("/auth/me")
    assert res.status_code == 401


def test_logout_clears_the_session(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.get("/auth/me", cookies=_auth(hr_token))
    assert res.status_code == 200

    # Logout only clears whatever's in the shared client's cookie jar
    # (i.e. the actual browser flow) - it doesn't invalidate a token
    # value used explicitly via `cookies=` elsewhere, same as a real
    # httpOnly cookie can't be selectively revoked per copy of itself.
    res = client.post("/auth/logout")
    assert res.status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_logout_finalizes_an_in_progress_round_instead_of_leaving_it_resumable(client, monkeypatch):
    """The real behavior change: logging out is no longer a free pause.
    Whatever's in progress at that moment gets scored as-is immediately -
    same finalize_abandoned_submission path a genuine timeout uses (see
    routers/auth.py's logout) - not left open for the candidate to
    resume by logging back in."""
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 0, "misses": ["Nothing submitted"], "final_score": 0, "feedback_text": "no attempt"},
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    assert res.status_code == 201

    # Candidate logs out without ever submitting.
    res = client.post("/auth/logout", cookies=_auth(cand_token))
    assert res.status_code == 204

    # Logging back in does NOT resume the round - it's already finalized.
    cand_token2 = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    state = client.get("/candidate/round/1", cookies=_auth(cand_token2)).json()
    assert state["submission"]["status"] == "scored"

    report = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in report if c["email"] == CANDIDATE1_EMAIL)
    round1 = next(r for r in row["rounds"] if r["round_number"] == 1)
    assert round1["status"] == "scored"
    assert round1["final_score"] == 0
    assert round1["auto_closed_reason"] == "Candidate logged out before completing this round"


def test_logout_on_an_expired_session_still_clears_the_cookie(client):
    """The 401-triggered logout path (see app.js's api()) sends a token
    that can no longer be decoded - logout must still succeed and clear
    the cookie rather than erroring, it just has nothing to finalize."""
    res = client.post("/auth/logout", cookies={"qa_eval_token": "not-a-real-token"})
    assert res.status_code == 204


def test_second_login_kicks_out_the_first_session_mid_round(client, monkeypatch):
    """The actual concurrent-device concern: a candidate logged in on two
    devices at once during a timed round. Logging in again anywhere
    invalidates whichever session was previously "active" (see
    models.User.active_session_id / dependencies.get_current_user) - but
    only once there's a round in_progress to protect."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)

    device_a_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.post("/candidate/round/1/start", cookies=_auth(device_a_token))
    assert res.status_code == 201

    # Device B (a fresh login, e.g. someone else's phone) logs in - device
    # A's session is now the stale one.
    device_b_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)

    res = client.get("/candidate/round/1", cookies=_auth(device_a_token))
    assert res.status_code == 401
    assert "another device" in res.json()["detail"]

    # The newest login works completely normally.
    res = client.get("/candidate/round/1", cookies=_auth(device_b_token))
    assert res.status_code == 200
    assert res.json()["submission"]["status"] == "in_progress"


def test_a_second_login_replaces_the_first_session_even_between_rounds(client, monkeypatch):
    """Changed 2026-09-27 (journey matrix): between rounds the old session used to
    keep working, so a logged-out or replaced session - an old tab whose timer
    fires, a second device - could still start and submit a round. Only the
    latest session acts; the old one gets a clear message and signs itself out
    without touching the new one (see test_a_replaced_sessions_logout_...)."""
    _login(client, HR_EMAIL, HR_PASSWORD)
    device_a_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    device_b_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.cookies.clear()
    old = client.get("/auth/me", cookies=_auth(device_a_token))
    assert old.status_code == 401 and "another device" in old.json()["detail"]
    client.cookies.clear()
    assert client.get("/auth/me", cookies=_auth(device_b_token)).status_code == 200


def test_hr_can_hold_multiple_sessions_freely(client):
    """Single-active-session is candidate-only (see
    dependencies.get_current_user) - HR juggling several tabs is normal,
    not a round-integrity concern."""
    hr_token_1 = _login(client, HR_EMAIL, HR_PASSWORD)
    hr_token_2 = _login(client, HR_EMAIL, HR_PASSWORD)
    assert client.get("/auth/me", cookies=_auth(hr_token_1)).status_code == 200
    assert client.get("/auth/me", cookies=_auth(hr_token_2)).status_code == 200


def test_candidate_can_log_in_with_derived_username(client, monkeypatch):
    """Bulk-uploaded candidates log in with a plain username (email's
    local part), not their email - see credential_service.py and
    routers/auth.py's OR lookup. Covered end-to-end once bulk upload
    exists (see test_bulk_upload.py); this is the login-side contract
    alone, exercised directly against a manually-created username to
    keep this test independent of the upload feature."""
    from app.database import SessionLocal
    from app.models import User, Role
    from app.security import hash_password

    db = SessionLocal()
    db.add(User(
        email="jane.doe@example.com", username="jane.doe",
        password_hash=hash_password("idfc@jane"), role=Role.candidate,
    ))
    db.commit()
    db.close()

    res = client.post("/auth/login", json={"identifier": "jane.doe", "password": "idfc@jane"})
    assert res.status_code == 200
    assert res.json()["role"] == "candidate"

    # Logging in with the full email still works too - both columns are checked.
    res = client.post("/auth/login", json={"identifier": "jane.doe@example.com", "password": "idfc@jane"})
    assert res.status_code == 200


def test_a_replaced_sessions_logout_never_ends_the_current_sessions_round(client, monkeypatch):
    """Measured 2026-09-27: a candidate was signed in again elsewhere; the old
    session's automatic 401 logout closed the Round 1 just started in the new
    session as "logged out before completing" - twice. An old session now only
    signs itself out; the current session's own logout still ends the round."""
    from app.services import llm_service
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch)
    monkeypatch.setattr(llm_service, "score_round1_submission",
                        lambda **kwargs: {"coverage_score": 0, "misses": [], "final_score": 0, "feedback_text": "x"})

    old_session = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    new_session = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)  # a later login elsewhere
    client.cookies.clear()
    assert client.post("/candidate/round/1/start", cookies=_auth(new_session)).status_code == 201

    client.cookies.clear()
    assert client.post("/auth/logout", cookies=_auth(old_session)).status_code == 204
    client.cookies.clear()
    state = client.get("/candidate/round/1", cookies=_auth(new_session))
    assert state.status_code == 200, "the current session must still be signed in"
    assert state.json()["submission"]["status"] == "in_progress"

    client.cookies.clear()
    assert client.post("/auth/logout", cookies=_auth(new_session)).status_code == 204  # its own logout still ends it
    again = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.cookies.clear()
    assert client.get("/candidate/round/1", cookies=_auth(again)).json()["submission"]["status"] != "in_progress"
