"""A Round 1 scenario going live again reuses the Round 2 environment and
screens already generated from its exact text, instead of paying for two AI
calls to regenerate them (hr.py's _resync_round2_automation_reference_for_band).
Anything that could change the result - the text, the prompts, the model -
means a fresh generation; HR's own Regenerate always generates."""
import pytest

from .conftest import HR_EMAIL, HR_PASSWORD, FAKE_UI_MOCKUP, _auth, _login, _publish_scenario, _publish_round4_scenario


def _setup(client, monkeypatch):
    """App A live with a Round 2 scenario grounded in it, then app B promoted
    live (one generation). Returns what's needed to promote A again."""
    from app.services import llm_service
    hr = _login(client, HR_EMAIL, HR_PASSWORD)
    app_a = _publish_scenario(client, hr, monkeypatch, round_number=1, title="App A", description="A library app")
    round2 = _publish_round4_scenario(client, hr, monkeypatch)
    app_b = _publish_scenario(client, hr, monkeypatch, round_number=1, title="App B", description="A bus booking app")

    calls = []

    def fake_env(**kwargs):
        calls.append(("env", kwargs["app_description"]))
        return {"fields": {"app": kwargs["app_description"]}, "notes": ""}

    def fake_mockup(**kwargs):
        calls.append(("mockup", kwargs["app_description"]))
        return dict(FAKE_UI_MOCKUP)

    monkeypatch.setattr(llm_service, "generate_round2_automation_environment", fake_env)
    monkeypatch.setattr(llm_service, "generate_round2_automation_ui_mockup", fake_mockup)
    assert client.post(f"/hr/scenarios/{app_b['id']}/move-to-screening", cookies=_auth(hr)).status_code == 200
    assert calls == [("env", "A bus booking app"), ("mockup", "A bus booking app")]
    calls.clear()
    return hr, app_a, app_b, round2, calls


def _environment(client, hr, round2):
    return client.get(f"/hr/scenarios/{round2['id']}", cookies=_auth(hr)).json()["environment_json"]


def test_same_round1_going_live_again_reuses_its_environment(client, monkeypatch):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    from .conftest import FAKE_ENVIRONMENT
    assert client.post(f"/hr/scenarios/{app_a['id']}/move-to-screening", cookies=_auth(hr)).status_code == 200
    assert calls == []
    assert _environment(client, hr, round2) == FAKE_ENVIRONMENT  # what was generated from A's text


def test_switching_back_and_forth_generates_each_text_once(client, monkeypatch):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    client.post(f"/hr/scenarios/{app_a['id']}/move-to-screening", cookies=_auth(hr))
    client.post(f"/hr/scenarios/{app_b['id']}/move-to-screening", cookies=_auth(hr))
    assert calls == []
    assert _environment(client, hr, round2) == {"fields": {"app": "A bus booking app"}, "notes": ""}


def test_different_model_generates_again(client, monkeypatch):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    from app.config import settings
    monkeypatch.setattr(settings, "claude_model", "claude-sonnet-5")
    client.post(f"/hr/scenarios/{app_a['id']}/move-to-screening", cookies=_auth(hr))
    assert calls == [("env", "A library app"), ("mockup", "A library app")]


@pytest.mark.parametrize("prompt_file", ["round2_automation_environment_generation.txt", "round2_automation_ui_mockup_generation.txt"])
def test_changed_prompt_generates_again(client, monkeypatch, prompt_file):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    from app.services import llm_service
    real_load = llm_service._load_prompt
    monkeypatch.setattr(llm_service, "_load_prompt", lambda name: real_load(name) + ("\nedited" if name == prompt_file else ""))
    client.post(f"/hr/scenarios/{app_a['id']}/move-to-screening", cookies=_auth(hr))
    assert calls == [("env", "A library app"), ("mockup", "A library app")]


def test_hr_regenerate_always_generates(client, monkeypatch):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    res = client.post(f"/hr/scenarios/{round2['id']}/regenerate-reference", cookies=_auth(hr))
    assert res.status_code == 200, res.text
    assert calls == [("env", "A bus booking app"), ("mockup", "A bus booking app")]


def test_hr_edited_environment_is_never_replaced_by_a_stored_one(client, monkeypatch):
    hr, app_a, app_b, round2, calls = _setup(client, monkeypatch)
    res = client.patch(f"/hr/scenarios/{round2['id']}/round4-environment",
                       json={"fields": {"login": "hr-pinned@example.com"}, "notes": ""}, cookies=_auth(hr))
    assert res.status_code == 200, res.text
    client.post(f"/hr/scenarios/{app_a['id']}/move-to-screening", cookies=_auth(hr))
    assert calls == []
    assert _environment(client, hr, round2)["fields"] == {"login": "hr-pinned@example.com"}
