"""Copy as new draft: a published scenario can't be edited (every candidate
must get the same version), so fixing an answer-key mistake like Loan TC-2
needed a hand-written database command (2026-09-27). HR now makes an editable
draft copy - no AI call - fixes it, and publishes the copy; the original and
every result on it stay as they were."""
import pytest

from app.services import llm_service
from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_round4_scenario, _publish_scenario


def test_a_published_scenario_becomes_an_editable_draft_copy(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    original = _publish_scenario(client, hr, monkeypatch, round_number=1, title="Loan EMI")
    before = client.get(f"/hr/scenarios/{original['id']}", cookies=_auth(hr)).json()
    assert before["status"] == "published"
    # published: the answer key can't be edited
    assert client.patch(f"/hr/scenarios/{original['id']}", json={"reference_json": []}, cookies=_auth(hr)).status_code == 400

    monkeypatch.setattr(llm_service, "_send", lambda *a, **k: pytest.fail("copying must not call the AI"))
    res = client.post(f"/hr/scenarios/{original['id']}/copy-as-draft", cookies=_auth(hr))
    assert res.status_code == 201
    copy = res.json()
    assert copy["id"] != original["id"] and copy["status"] == "draft" and copy["title"] == "Loan EMI (copy)"
    assert copy["reference_json"] == before["reference_json"] and copy["description"] == before["description"]
    assert copy["is_live"] is False and "practice_app" not in (copy.get("config_json") or {})

    fixed = [dict(before["reference_json"][0], expected_result="Outstanding ₹1,21,200.00")] + before["reference_json"][1:]
    assert client.patch(f"/hr/scenarios/{copy['id']}", json={"reference_json": fixed}, cookies=_auth(hr)).status_code == 200
    assert client.get(f"/hr/scenarios/{original['id']}", cookies=_auth(hr)).json()["reference_json"] == before["reference_json"]


def test_round2_cannot_be_copied_and_candidates_cannot_copy(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    r1 = _publish_scenario(client, hr, monkeypatch, round_number=1)
    round2 = _publish_round4_scenario(client, hr, monkeypatch)  # the automation round (slot 2)
    assert round2["round_number"] == 2
    assert client.post(f"/hr/scenarios/{round2['id']}/copy-as-draft", cookies=_auth(hr)).status_code == 400
    client.cookies.clear()
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.cookies.clear()
    assert client.post(f"/hr/scenarios/{r1['id']}/copy-as-draft", cookies=_auth(cand)).status_code == 403
    assert client.post("/hr/scenarios/999999/copy-as-draft", cookies=_auth(hr)).status_code == 404
