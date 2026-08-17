"""
HR's cross-round candidate summary + PDF export (see routers/hr.py's
/candidates/{id}/summary and /summary/pdf, llm_service.generate_candidate_summary).
Structured - one comment per round the candidate reached, plus a final
verdict paragraph - not one undifferentiated block of prose (see
prompts/candidate_summary_generation.txt). Never a real Claude call -
generate_candidate_summary is monkeypatched.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    FAKE_REFERENCE, _login, _auth, _publish_scenario,
)

FAKE_SUMMARY_RESULT = {
    "rounds": [
        {
            "round_number": 1,
            # deliberately includes an em dash and curly quotes - real Claude
            # output routinely does too, and fpdf2's default font can't
            # render them raw (see hr.py's _pdf_safe_text)
            "comment": "Strong boundary-case coverage — but missed the candidate’s own “empty cart” edge case.",
        },
    ],
    "final_summary": "Round 1 cleared with solid fundamentals; round 2 not yet attempted. Recommend advancing.",
}


def test_summary_requires_at_least_one_submission(client, monkeypatch):
    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)  # account exists, never starts anything

    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]

    res = client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 400


def test_summary_generated_and_downloadable_as_pdf(client, monkeypatch):
    from app.services import llm_service

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return dict(FAKE_SUMMARY_RESULT)

    monkeypatch.setattr(llm_service, "generate_candidate_summary", _capture)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {
            "coverage_score": 80, "misses": ["empty cart"], "final_score": 75, "feedback_text": "Solid.",
            "concept_coverage": [{"category": "Boundary", "total": 1, "covered": 0, "notes": "Missed empty cart."}],
        },
    )

    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Add item", "steps": "...", "expected_result": "..."}]},
        cookies=_auth(cand_token),
    )

    candidates = client.get("/hr/candidates", cookies=_auth(hr_token)).json()
    candidate_id = next(c for c in candidates if c["email"] == CANDIDATE1_EMAIL)["id"]

    res = client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 200
    body = res.json()
    assert body["candidate_email"] == CANDIDATE1_EMAIL
    assert len(body["round_comments"]) == 1
    assert body["round_comments"][0]["round_number"] == 1
    assert body["round_comments"][0]["comment"] == FAKE_SUMMARY_RESULT["rounds"][0]["comment"]
    assert body["final_summary"] == FAKE_SUMMARY_RESULT["final_summary"]

    # The LLM call was grounded in the real round data, not a stub.
    assert captured["candidate_email"] == CANDIDATE1_EMAIL
    rounds = captured["rounds"]
    assert len(rounds) == 1  # round 1 only - round 2/3 never started, omitted entirely
    assert rounds[0]["round_number"] == 1
    assert rounds[0]["final_score"] == 75
    assert rounds[0]["misses"] == ["empty cart"]
    # Per-category coverage (see _gather_candidate_rounds) is passed
    # through to the summary prompt too, so it can call out strengths/
    # weaknesses by name.
    assert rounds[0]["concept_coverage"] == [{"category": "Boundary", "total": 1, "covered": 0, "notes": "Missed empty cart."}]
    assert rounds[0]["scenario_title"] == "Checkout flow"

    res = client.post(
        f"/hr/candidates/{candidate_id}/summary/pdf",
        json={"round_comments": body["round_comments"], "final_summary": body["final_summary"]},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert len(res.content) > 0
    assert res.content.startswith(b"%PDF-")
