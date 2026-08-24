"""
POST /hr/scenarios - empty title/description must be rejected with 400
BEFORE any reference-generation LLM call happens (see hr.py's
create_scenario). Previously these two fields had no validation at all -
an empty title/description would create the Scenario row and then run a
real, ~20-30s LLM call against it before finally failing (or not failing
at all, since the LLM would still happily generate SOMETHING from an
empty prompt). The monkeypatch below asserts the generator is never even
called, which is the part a plain "expect 400" test would miss - a 400
that still burned the LLM call would pass a status-code-only test but
still be the bug.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from .conftest import HR_EMAIL, HR_PASSWORD, _login, _auth


def test_create_scenario_rejects_empty_title_without_calling_the_llm(client, monkeypatch):
    from app.services import llm_service

    called = []
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: called.append(1) or [])

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "", "description": "A real description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400
    assert "title" in res.json()["detail"].lower()
    assert called == []


def test_create_scenario_rejects_whitespace_only_title(client, monkeypatch):
    from app.services import llm_service

    called = []
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: called.append(1) or [])

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "   ", "description": "A real description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400
    assert called == []


def test_create_scenario_rejects_empty_description_without_calling_the_llm(client, monkeypatch):
    from app.services import llm_service

    called = []
    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: called.append(1) or [])

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "A real title", "description": "", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 400
    assert "description" in res.json()["detail"].lower()
    assert called == []


def test_create_scenario_still_works_with_real_title_and_description(client, monkeypatch):
    from app.services import llm_service
    from .conftest import FAKE_REFERENCE

    monkeypatch.setattr(llm_service, "generate_round1_reference", lambda **kwargs: list(FAKE_REFERENCE))

    hr_token = _login(client, HR_EMAIL, HR_PASSWORD)
    res = client.post(
        "/hr/scenarios",
        json={"round_number": 1, "title": "A real title", "description": "A real description", "experience_band": "0-7", "time_limit_minutes": 30},
        cookies=_auth(hr_token),
    )
    assert res.status_code == 201
