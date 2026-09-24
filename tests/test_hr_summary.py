"""
HR's cross-round candidate summary + PDF export (see routers/hr.py's
GET/POST/DELETE /candidates/{id}/summary and POST .../summary/pdf,
models.CandidateSummary, llm_service.generate_candidate_summary).
Structured as scannable bullets per round (did_well/missed) plus
cross-round key_observations and a short verdict - not paragraphs of
prose (see prompts/candidate_summary_generation.txt). Never a real
Claude call - generate_candidate_summary is monkeypatched.

Persisted, not regenerated on every visit - POST saves it, GET reads the
saved copy back with no LLM call, DELETE clears it, and the PDF always
renders whatever's currently saved rather than taking a body.
"""
from .conftest import (
    HR_EMAIL, HR_PASSWORD,
    CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD,
    _login, _auth, _publish_scenario,
)

FAKE_SUMMARY_RESULT = {
    "rounds": [
        {
            "round_number": 1,
            # deliberately includes an em dash and curly quotes - real Claude
            # output routinely does too, and fpdf2's default font can't
            # render them raw (see hr.py's _pdf_safe_text)
            "did_well": ["Strong boundary-case coverage — solid fundamentals."],
            "missed": ["Missed the candidate’s own “empty cart” edge case."],
        },
    ],
    "key_observations": ["Round 1 cleared with solid fundamentals; round 2 not yet attempted."],
    "verdict": "Recommend advancing.",
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
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
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
    assert body["round_comments"][0]["did_well"] == FAKE_SUMMARY_RESULT["rounds"][0]["did_well"]
    assert body["round_comments"][0]["missed"] == FAKE_SUMMARY_RESULT["rounds"][0]["missed"]
    assert body["key_observations"] == FAKE_SUMMARY_RESULT["key_observations"]
    assert body["verdict"] == FAKE_SUMMARY_RESULT["verdict"]

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
    assert body["generated_at"] is not None

    res = client.post(f"/hr/candidates/{candidate_id}/summary/pdf", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert len(res.content) > 0
    assert res.content.startswith(b"%PDF-")


def test_summary_persists_across_requests_with_no_further_llm_calls(client, monkeypatch):
    """The actual point of persisting it: a GET later (a fresh page load,
    HR coming back to this candidate tomorrow) reads back exactly what
    was generated, without calling the LLM again."""
    from app.services import llm_service

    call_count = {"n": 0}

    def _capture(**kwargs):
        call_count["n"] += 1
        return dict(FAKE_SUMMARY_RESULT)

    monkeypatch.setattr(llm_service, "generate_candidate_summary", _capture)

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    _publish_scenario(client, hr_token, monkeypatch, title="Checkout flow")
    monkeypatch.setattr(
        llm_service, "score_round1_submission",
        lambda **kwargs: {"coverage_score": 80, "misses": [], "final_score": 75, "feedback_text": "Solid."},
    )
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.post("/candidate/round/1/start", cookies=_auth(cand_token))
    client.post(
        "/candidate/round/1/submit",
        json={"content": [{"title": "Add item", "steps": "...", "expected_result": "..."}]},
        cookies=_auth(cand_token),
    )
    candidate_id = next(c for c in client.get("/hr/candidates", cookies=_auth(hr_token)).json() if c["email"] == CANDIDATE1_EMAIL)["id"]

    # No summary yet - GET/PDF both 404 rather than generating one.
    assert client.get(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token)).status_code == 404
    assert client.post(f"/hr/candidates/{candidate_id}/summary/pdf", cookies=_auth(hr_token)).status_code == 404

    generated = client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token)).json()
    assert call_count["n"] == 1

    # A completely separate GET reads the same content back - no second LLM call.
    fetched = client.get(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token)).json()
    assert call_count["n"] == 1
    assert fetched["verdict"] == generated["verdict"]
    assert fetched["key_observations"] == generated["key_observations"]
    assert fetched["round_comments"] == generated["round_comments"]

    # The PDF now works too, still without another LLM call.
    res = client.post(f"/hr/candidates/{candidate_id}/summary/pdf", cookies=_auth(hr_token))
    assert res.status_code == 200
    assert call_count["n"] == 1

    # Regenerating (POST again) calls the LLM again and overwrites the saved copy.
    client.post(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert call_count["n"] == 2

    # Deleting clears it - GET and the PDF both go back to 404.
    res = client.delete(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 204
    assert client.get(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token)).status_code == 404
    assert client.post(f"/hr/candidates/{candidate_id}/summary/pdf", cookies=_auth(hr_token)).status_code == 404

    # Deleting again (nothing left to delete) is a harmless no-op.
    res = client.delete(f"/hr/candidates/{candidate_id}/summary", cookies=_auth(hr_token))
    assert res.status_code == 204


def test_summary_endpoints_require_hr(client):
    cand_token = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    assert client.get("/hr/candidates/999999/summary", cookies=_auth(cand_token)).status_code == 403
    assert client.post("/hr/candidates/999999/summary", cookies=_auth(cand_token)).status_code == 403
    assert client.delete("/hr/candidates/999999/summary", cookies=_auth(cand_token)).status_code == 403
    assert client.post("/hr/candidates/999999/summary/pdf", cookies=_auth(cand_token)).status_code == 403
