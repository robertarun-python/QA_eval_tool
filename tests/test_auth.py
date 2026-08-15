from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD


def test_seeded_hr_can_log_in(client):
    res = client.post("/auth/login", json={"email": HR_EMAIL, "password": HR_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "hr"
    assert "access_token" in res.json()


def test_seeded_candidate_can_log_in(client):
    res = client.post("/auth/login", json={"email": CANDIDATE1_EMAIL, "password": CANDIDATE1_PASSWORD})
    assert res.status_code == 200
    assert res.json()["role"] == "candidate"


def test_wrong_password_rejected(client):
    res = client.post("/auth/login", json={"email": CANDIDATE1_EMAIL, "password": "definitely-wrong"})
    assert res.status_code == 401


def test_unknown_email_rejected(client):
    res = client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever"})
    assert res.status_code == 401


def test_no_signup_route(client):
    # Accounts are seeded (app/seed.py) - there is no public signup endpoint.
    res = client.post("/auth/signup", json={"email": "x@example.com", "password": "y", "role": "candidate"})
    assert res.status_code == 404


def test_hr_only_endpoint_rejects_candidate(client):
    token = client.post(
        "/auth/login", json={"email": CANDIDATE1_EMAIL, "password": CANDIDATE1_PASSWORD}
    ).json()["access_token"]

    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "x", "description": "y", "experience_band": "0-7"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403
