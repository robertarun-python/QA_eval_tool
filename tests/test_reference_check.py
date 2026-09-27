"""Round 1 test cases that contradict each other (services.reference_check):
Loan TC-2 and TC-10 promised different "outstanding" figures after the same
EMI and both sat in the published answer key (2026-09-27). Flagged in HR's
review, before any build, with no AI call."""
import json
from pathlib import Path

from app.services.reference_check import contradictions
from .conftest import CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD, HR_EMAIL, HR_PASSWORD, _auth, _login, _publish_scenario

LOAN = json.loads((Path(__file__).parent / "fixtures" / "loan_reference_before_tc2_fix.json").read_text())
BREAKERS = json.loads((Path(__file__).parent / "stress" / "breaker_scenarios.json").read_text())


def test_the_real_loan_contradiction_is_found():
    [found] = contradictions(LOAN)
    assert "Make successful EMI payment with sufficient balance" in found and "partial prepayment" in found
    assert "119,750.00" in found and "121,200.00" in found


def test_once_fixed_it_is_quiet():
    fixed = json.loads(json.dumps(LOAN))
    fixed[1]["expected_result"] = fixed[1]["expected_result"].replace("₹1,19,750.00", "₹1,21,200.00")
    assert contradictions(fixed) == []


def test_no_false_alarms_on_the_test_scenarios():
    """17 scenarios written to break things (tests/stress): none contradicts itself."""
    for scenario in BREAKERS:
        assert contradictions(scenario["cases"]) == [], scenario["key"]


def test_odd_shapes_never_crash():
    for cases in (None, [], [None], ["x"], [{}], [{"expected_result": None, "steps": 5}], [{"test_data": "1,000"}, {"test_data": "1,000"}]):
        assert contradictions(cases) == []


def test_hr_sees_them_and_candidates_cannot(client, monkeypatch):
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    scenario = _publish_scenario(client, hr, monkeypatch, round_number=1)
    from app import database
    from app.models import Scenario
    db = database.SessionLocal()
    try:
        db.get(Scenario, scenario["id"]).reference_json = LOAN
        db.commit()
    finally:
        db.close()
    assert len(client.get(f"/hr/scenarios/{scenario['id']}/reference-check", cookies=_auth(hr)).json()["contradictions"]) == 1
    client.cookies.clear()
    cand = _login(client, CANDIDATE1_EMAIL, CANDIDATE1_PASSWORD)
    client.cookies.clear()
    assert client.get(f"/hr/scenarios/{scenario['id']}/reference-check", cookies=_auth(cand)).status_code == 403
