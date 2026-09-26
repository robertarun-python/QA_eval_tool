"""
Prompt caching: a CACHE_BREAK line in a prompt splits it into blocks, every
block before the last marked cacheable, so a repeat of the same start (e.g.
the practice app's code on each Round 2 turn) is billed at the cache price.
The model must see exactly the same text either way.
"""
from types import SimpleNamespace

from app.services import llm_service

B = llm_service.CACHE_BREAK


def test_a_prompt_without_breaks_is_sent_as_before():
    assert llm_service._user_content("plain prompt") == "plain prompt"


def test_everything_before_a_break_is_cacheable_and_the_text_is_unchanged():
    prompt = f"instructions\nenvironment code\n---\n{B}\nTHE CURRENT CODE\n..."
    blocks = llm_service._user_content(prompt)
    assert [b.get("cache_control") for b in blocks] == [{"type": "ephemeral"}, None]
    assert "".join(b["text"] for b in blocks) == prompt.replace(B, "")


def test_at_most_three_breakpoints_are_used():
    blocks = llm_service._user_content(B.join("abcdef"))
    assert sum("cache_control" in b for b in blocks) == 3
    assert "".join(b["text"] for b in blocks) == "abcdef"


def test_the_round2_prompts_break_right_after_the_practice_app_code():
    for name in ("round2_automation_turn.txt", "round2_automation_clarify.txt"):
        text = llm_service._load_prompt(name)
        before, after = text.split(B)
        assert before.rstrip().endswith("{environment_code}\n---") and "{current_code}" in after
        assert "{conversation_so_far}" in after and "{candidate_prompt}" in after  # the per-turn parts come after


def test_the_request_carries_the_blocks_and_the_log_records_cached_tokens(monkeypatch):
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        usage = SimpleNamespace(input_tokens=50, output_tokens=10, cache_read_input_tokens=7000, cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn", usage=usage)

    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))
    llm_service._CALL_LOG.clear()
    assert llm_service._call_claude(f"big shared part{B}the turn") == "ok"
    assert sent["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    entry = llm_service.recent_calls()[0]
    assert entry["cache_read_tokens"] == 7000 and entry["cache_write_tokens"] == 0


def test_fake_mode_never_sees_the_marker(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_service.settings, "llm_fake_mode", True)
    monkeypatch.setattr(llm_service.fake_llm, "reply_text", lambda caller, prompt: seen.append(prompt) or "x")
    llm_service._call_claude(f"a{B}b")
    assert seen == ["ab"]


def test_hr_sees_the_estimated_cost_and_what_caching_saved(client):
    from .conftest import HR_EMAIL, HR_PASSWORD, _auth, _login
    llm_service._CALL_LOG.clear()
    llm_service._CALL_LOG.append({"outcome": "ok", "caller": "round2_automation_turn", "input_tokens": 1000, "output_tokens": 1000,
                                  "cache_read_tokens": 1_000_000, "cache_write_tokens": 0})
    health = client.get("/hr/ai-health", cookies=_auth(_login(client, HR_EMAIL, HR_PASSWORD))).json()
    assert health["saved_by_cache_usd"] == 2.7  # 1M cached tokens at 0.9 x $3/M
    assert health["estimated_cost_usd"] == round((1000 * 3 + 1000 * 15 + 1_000_000 * 0.3) / 1e6, 2)
