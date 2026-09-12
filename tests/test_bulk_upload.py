"""
HR's bulk candidate upload (see routers/hr.py's POST /candidates/upload,
services/candidate_upload_service.py). No LLM calls except where a full
round is completed (mocked, same pattern as the other test files).
"""
import io

import openpyxl

from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth, _publish_scenario


def _txt_file(text: str):
    return {"file": ("candidates.txt", text.encode(), "text/plain")}


def _xlsx_file(rows: list[tuple]):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["email", "exam_date"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return {"file": ("candidates.xlsx", buf.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


def test_txt_upload_creates_candidates_who_can_then_log_in(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    text = "email,exam_date\njohn.doe@acme.com,2026-08-25\njane.smith@acme.com,2026-08-26\n"

    res = client.post("/hr/candidates/upload", files=_txt_file(text), cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body["created_count"] == 2
    assert body["error_count"] == 0
    assert {r["username"] for r in body["rows"]} == {"john.doe", "jane.smith"}
    john_password = next(r["password"] for r in body["rows"] if r["username"] == "john.doe")

    # The generated credentials actually work.
    res = client.post("/auth/login", json={"identifier": "john.doe", "password": john_password})
    assert res.status_code == 200
    assert res.json()["role"] == "candidate"

    # And they show up on the candidates dashboard with their exam date.
    res = client.get("/hr/candidates", cookies=_auth(hr_token))
    row = next(c for c in res.json() if c["email"] == "john.doe@acme.com")
    assert row["exam_date"].startswith("2026-08-25")
    # Experience band is a hidden feature now (see candidate_upload_service.py) -
    # every new candidate gets the same band automatically, not left unset.
    assert row["experience_band"] == "0-7"


def test_xlsx_upload_creates_candidates(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    files = _xlsx_file([("alice@acme.com", "2026-09-01"), ("bob@acme.com", "2026-09-02")])

    res = client.post("/hr/candidates/upload", files=files, cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body["created_count"] == 2
    alice_password = next(r["password"] for r in body["rows"] if r["username"] == "alice")

    res = client.post("/auth/login", json={"identifier": "alice", "password": alice_password})
    assert res.status_code == 200


def test_row_level_errors_do_not_block_other_valid_rows(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    text = (
        "email,exam_date\n"
        "good.one@acme.com,2026-08-25\n"
        "not-an-email,2026-08-25\n"
        "good.two@acme.com,not-a-date\n"
        "good.one@acme.com,2026-08-26\n"  # duplicate within the file
        "good.three@acme.com,2026-08-27\n"
    )
    res = client.post("/hr/candidates/upload", files=_txt_file(text), cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body["created_count"] == 2  # good.one, good.three
    assert body["error_count"] == 3
    errors_by_row = {r["row_number"]: r["error"] for r in body["rows"] if r["status"] == "error"}
    assert len(errors_by_row) == 3


def test_username_collision_with_different_email_is_rejected(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    client.post("/hr/candidates/upload", files=_txt_file("email,exam_date\njohn.doe@acme.com,2026-08-25\n"), cookies=_auth(hr_token))

    # Different domain, same local part -> same derived username.
    res = client.post(
        "/hr/candidates/upload",
        files=_txt_file("email,exam_date\njohn.doe@other.org,2026-08-25\n"),
        cookies=_auth(hr_token),
    )
    body = res.json()
    assert body["created_count"] == 0
    assert body["error_count"] == 1
    assert "already in use" in body["rows"][0]["error"]


def test_reupload_resets_and_archives_with_reapplied_flag(client, monkeypatch):
    from app.services import llm_service

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, title="R1 scenario")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 90, "misses": [], "final_score": 90, "feedback_text": "great"},
    )

    upload_body = client.post("/hr/candidates/upload", files=_txt_file("email,exam_date\njohn.doe@acme.com,2026-01-01\n"), cookies=_auth(hr_token)).json()
    john_password = next(r["password"] for r in upload_body["rows"] if r["username"] == "john.doe")
    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == "john.doe@acme.com")["id"]
    # A band has to be set before the candidate can see any scenario at
    # all - bulk upload deliberately leaves it unset (see the upload endpoint).
    client.patch(f"/hr/candidates/{candidate_id}/band", json={"experience_band": "0-7"}, cookies=_auth(hr_token))

    # Candidate completes round 1 for real.
    cand_token = _login(client, "john.doe", john_password)
    res = client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    assert res.status_code == 201
    res = client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "x", "steps": "x", "expected_result": "x"}]},
        cookies=_auth(cand_token),
    )
    assert res.status_code == 201
    res = client.get("/candidate/round/2", cookies=_auth(cand_token))
    assert res.status_code == 200  # round 1 cleared, round 2 unlocked

    # Re-upload the same email, exam date well within the default 6-month window.
    res = client.post(
        "/hr/candidates/upload",
        files=_txt_file("email,exam_date\njohn.doe@acme.com,2026-02-01\n"),
        cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    assert res.json()["reset_count"] == 1

    # Same login still works, same password - nothing about credentials changed.
    cand_token2 = _login(client, "john.doe", john_password)

    # Round-gating reset: round 1 looks not-started again, not "already submitted".
    res = client.get("/candidate/round/1", cookies=_auth(cand_token2))
    assert res.json()["submission"] is None
    res = client.get("/candidate/round/2", cookies=_auth(cand_token2))
    assert res.status_code == 403  # locked again - round 1 hasn't been redone yet

    # HR's live dashboard/report reflect the fresh cycle, not the old one.
    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    row = next(c for c in candidates if c["email"] == "john.doe@acme.com")
    assert row["reapplied_within_window"] is True
    assert row["rounds"][0]["status"] == "not_started"

    # But the old cycle is still visible in "past appearances".
    appearances = client.get(f"/hr/candidates/{row['id']}/appearances", cookies=_auth(hr_token)).json()
    assert len(appearances) == 2
    past = next(a for a in appearances if not a["is_current"])
    assert past["aggregate_score"] == 90

    report = client.get(
        f"/hr/candidates/{row['id']}/appearances/{past['id']}/report", cookies=_auth(hr_token)
    ).json()
    assert len(report) == 1
    assert report[0]["round_number"] == 1
    assert report[0]["score"]["final_score"] == 90


def test_band_can_be_set_after_upload(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    client.post("/hr/candidates/upload", files=_txt_file("email,exam_date\njohn.doe@acme.com,2026-08-25\n"), cookies=_auth(hr_token))
    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == "john.doe@acme.com")["id"]

    res = client.patch(f"/hr/candidates/{candidate_id}/band", json={"experience_band": "7+"}, cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.json()["experience_band"] == "7+"


def test_upload_requires_hr(client):
    from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.post(
        "/hr/candidates/upload",
        files=_txt_file("email,exam_date\nx@example.com,2026-08-25\n"),
        cookies=_auth(cand_token),
    )
    assert res.status_code == 403
