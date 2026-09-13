"""
HR's daily cohort summary PDF (see routers/hr.py's GET
/hr/reports/daily-summary) - one row per candidate whose CURRENT
appearance's exam_date matches the requested date, with marks (reusing
_build_candidate_summary's aggregate_score/result - the same logic the
Candidates dashboard uses), a comment (reusing any AI summary already
generated for that candidate, never triggering a new one), and the
final result. No LLM calls in these tests - candidates here never have
an AI summary generated, so the comment column is always "Not generated
yet"; that path itself is exercised, not mocked around.
"""
from datetime import datetime, timedelta

from .conftest import HR_EMAIL, HR_PASSWORD, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, _login, _auth


def _date(days_from_now: int) -> str:
    """Relative to today, not a hardcoded literal - see test_bulk_upload's
    identical helper/comment for why a fixed past/future date eventually
    goes stale as real time passes."""
    return (datetime.utcnow() + timedelta(days=days_from_now)).strftime("%Y-%m-%d")


def _txt_file(text: str):
    return {"file": ("candidates.txt", text.encode(), "text/plain")}


def test_daily_summary_requires_hr(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    res = client.get(f"/hr/reports/daily-summary?exam_date={_date(1)}", cookies=_auth(cand_token))
    assert res.status_code == 403


def test_daily_summary_404_when_no_candidate_has_that_exam_date(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.get(f"/hr/reports/daily-summary?exam_date={_date(30)}", cookies=_auth(hr_token))
    assert res.status_code == 404


def test_daily_summary_returns_a_pdf_for_a_date_with_candidates(client):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    day = _date(2)
    client.post(
        "/hr/candidates/upload",
        files=_txt_file(f"email,exam_date\ncohort.a@example.com,{day}\ncohort.b@example.com,{day}\n"),
        cookies=_auth(hr_token),
    )

    res = client.get(f"/hr/reports/daily-summary?exam_date={day}", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF-")
    assert len(res.content) > 0


def test_daily_summary_is_scoped_to_the_requested_date_only(client):
    """Two candidates on two different exam dates - each date's own
    summary must succeed independently (proves the day_start/day_end
    range in daily_summary_pdf actually matches each candidate's own
    date, not just "some candidate exists somewhere")."""
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    day_one, day_two = _date(3), _date(4)
    client.post(
        "/hr/candidates/upload",
        files=_txt_file(f"email,exam_date\nscoped.a@example.com,{day_one}\nscoped.b@example.com,{day_two}\n"),
        cookies=_auth(hr_token),
    )

    assert client.get(f"/hr/reports/daily-summary?exam_date={day_one}", cookies=_auth(hr_token)).status_code == 200
    assert client.get(f"/hr/reports/daily-summary?exam_date={day_two}", cookies=_auth(hr_token)).status_code == 200
    # A third date nobody used still 404s - the two 200s above aren't just
    # "any candidate exists" leaking through.
    assert client.get(f"/hr/reports/daily-summary?exam_date={_date(5)}", cookies=_auth(hr_token)).status_code == 404
