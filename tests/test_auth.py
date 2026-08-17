from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD


def test_seeded_hr_can_log_in(client):
    res = client.post("/auth/login", json={"identifier": HR_EMAIL, "password": HR_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "hr"
    assert "access_token" in res.json()


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
    token = client.post(
        "/auth/login", json={"identifier": CANDIDATE1_EMAIL, "password": CANDIDATE1_PASSWORD}
    ).json()["access_token"]

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "x", "description": "y", "experience_band": "0-7"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


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
