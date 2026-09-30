"""
D0 + D1 + D2 (owner-approved, 2026-09-30), from the live smoke test where 8 of 13 Claude calls were redrafts:
  D0  every judged draft leaves a record of the rule(s) that fired and on what, joined to its call by call_id
  D1  "automate thet test case selected" and other ways of pointing at the selected Round 1 test case count as
      the candidate pointing at it (always anchored on "test case" + selected/chosen)
  D2  a message the fallback stores as steps is split into one step per non-empty line, every word kept
The real messages are in tests/fixtures/round2_live_smoke_2026_09_30.json. The model is scripted - its drafts
here are realistic stand-ins, not the recorded ones (those weren't kept then). No API call.
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import llm_service, round2_typist as rt

SMOKE = json.loads((Path(__file__).parent / "fixtures" / "round2_live_smoke_2026_09_30.json").read_text())
DESIGN = SMOKE["design"]
MSGS = [t["message"] for t in SMOKE["turns"]]
REF = ("APPLICATION: Online Loan EMI Payment System\nTest accounts (as the Reference shows them): login CUST001 / password Pass@123 (Test User)\n"
       'PAGE Login (/page/login):\n  input labelled "Customer id" id=customer_id\n  input labelled "Password" id=password\n'
       '  button text "Log in" id=login\nPAGE EMI Details (/page/emi-details):\n  a text "EMI Details" id=nav-emi-details\n'
       '  dd labelled "Account number" id=detail-account-number\n  dd labelled "Emi amount" id=detail-emi-amount')


# ---- D1 ----

@pytest.mark.parametrize("message", ["automate thet test case selected", "automate the selected test case", "selected test case please",
                                     "test case selected", "the chosen test case", "teh selected test case", "tha selected test case",
                                     "test case I selected", "Automate The Selected Test Case"])
def test_ways_of_pointing_at_the_selected_round1_case_are_recognised(message):
    assert rt._OWN_DESIGN_RE.search(message)
    assert DESIGN["test_data"] in rt.candidate_text(DESIGN, [], message)


@pytest.mark.parametrize("message", ["write a test case for login", "a test case", "selected tests", "the selected case",
                                     "is this a good case?", "log in and check the test user"])
def test_other_mentions_are_not_a_reference_to_the_round1_case(message):
    assert not rt._OWN_DESIGN_RE.search(message)


def test_the_real_message_no_longer_makes_the_round1_values_look_like_hints():
    said = rt.candidate_text(DESIGN, [], MSGS[0])
    reply = "I'll log in, click EMI Details and check the Emi amount is 5250.00 for LN-45678."
    assert rt.unsaid(reply, said, code=False) == []                      # was ['LN-45678'] before D1
    assert "BK-009" in rt.unsaid("Should the booking be BK-009?", said, code=False)   # a real hint is still one


# ---- D2 ----

HINT = {"reply": "Should the booking be BK-009?", "steps": [{"step": "Book BK-009", "missing": ""}], "code": None}  # always blocked

class Model:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, max_tokens=None, schema=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def test_the_fallback_stores_a_multiline_message_as_one_step_per_line(monkeypatch):
    monkeypatch.setattr(llm_service, "_call_claude_json", Model(HINT))
    out = rt.turn("python", {"title": "x"}, [], "", "Login to the app successfully\n\nClick on EMI Details  \n  Look for label Account number")
    assert [s["step"] for s in out["steps"]] == ["Login to the app successfully", "Click on EMI Details", "Look for label Account number"]


def test_the_split_keeps_every_word_and_value_in_order(monkeypatch):
    message = "Customer id - CUST001\nPassword -  Pass@123\n<form id=\"login-form\">"
    monkeypatch.setattr(llm_service, "_call_claude_json", Model(HINT))
    steps = [s["step"] for s in rt.turn("python", {"title": "x"}, [], "", message)["steps"]]
    assert steps == ["Customer id - CUST001", "Password - Pass@123", '<form id="login-form">']
    assert " ".join(steps).split() == message.split()


def test_a_genuinely_dropped_line_step_is_still_caught():
    prior = [{"step": "Login to the app successfully", "missing": ""}, {"step": "Click on EMI Details", "missing": ""},
             {"step": "Look for label Account number", "missing": ""}]
    assert rt._dropped(prior, prior[:2]) == ["Look for label Account number"]
    merged = [{"step": "Login to the app successfully and click on EMI Details", "missing": ""},
              {"step": "Look for label Account number", "missing": ""}]
    assert rt._dropped(prior, merged) == []                                 # merging two of them isn't dropping


# ---- the 5 real messages, replayed with a well-behaved scripted model ----

S = lambda *xs: [{"step": x, "missing": ""} for x in xs]
LOGIN = "Login to the app successfully"
STEPS1 = S(LOGIN, "Click on EMI Details", "Look for label Account number", "Look for label Emi amount")
STEPS3 = S(f"{LOGIN} with Customer id CUST001 and Password Pass@123", "Click on EMI Details", "Look for label Account number",
           "Look for label Emi amount")
STEPS4 = S(f"{LOGIN} with Customer id CUST001 and Password Pass@123", "Click on EMI Details",
           "Look for label Account number (check it exists)", "Look for label Emi amount (check it exists)")
PROGRAM = '''import os, sys
from selenium import webdriver
from selenium.webdriver.common.by import By
d = webdriver.Remote(os.environ["SELENIUM_GRID_URL"], options=webdriver.ChromeOptions())
try:
    d.get(os.environ["PRACTICE_APP_URL"])
    d.find_element(By.ID, "customer_id").send_keys("CUST001")
    d.find_element(By.ID, "password").send_keys("Pass@123")
    d.find_element(By.ID, "login").click()
    print("PASS: step 1 - Login to the app successfully with Customer id CUST001 and Password Pass@123")
    d.find_element(By.ID, "nav-emi-details").click()
    print("PASS: step 2 - Click on EMI Details")
    d.find_element(By.ID, "detail-account-number")
    print("PASS: Look for label Account number (check it exists)")
    d.find_element(By.ID, "detail-emi-amount")
    print("PASS: Look for label Emi amount (check it exists)")
    print("PASS: all steps and checks passed")
finally:
    d.quit()
'''
DRAFTS = [
    {"reply": "You want the selected test case automated: log in, click EMI Details, look for the Account number and Emi amount "
              "labels. Which Customer id and Password should the login use?", "steps": STEPS1, "code": None},
    {"reply": "Noted - those are the same four steps. I still need the Customer id and Password for the login.", "steps": STEPS1, "code": None},
    {"reply": "I'll log in with Customer id CUST001 and Password Pass@123. Shall I generate the code?", "steps": STEPS3, "code": None},
    {"reply": "The two label checks now only check they exist. Shall I generate the code?", "steps": STEPS4, "code": None},
    {"reply": "Here is the program for your steps.", "steps": STEPS4, "code": PROGRAM},
]


def _replay(monkeypatch):
    calls = []
    conversation, code = [], ""
    for message, draft in zip(MSGS, DRAFTS):
        model = Model(draft)
        monkeypatch.setattr(llm_service, "_call_claude_json", model)
        out = rt.turn(SMOKE["language"], DESIGN, conversation, code, message, app_reference=REF)
        calls.append(len(model.prompts))
        conversation.append({"candidate_prompt": message, "response_message": out["response_message"], "response_kind": out["response_kind"],
                             "steps": out["steps"]})
        code = out.get("code_after") or code
    return calls, out, conversation


def test_the_five_real_messages_need_no_redraft_now(monkeypatch):
    calls, last, conversation = _replay(monkeypatch)
    assert calls == [1, 1, 1, 1, 1]                                        # the live run needed [3, 3, 3, 2, 2]
    assert last["code_after"] == PROGRAM and "incomplete" not in last["code_after"]
    assert [t["response_message"] for t in conversation] == [d["reply"] for d in DRAFTS]   # no stand-in reply


def test_before_d1_the_first_real_message_cascaded(monkeypatch):
    """The same replay with the old reference pattern: message 1's drafts are blocked, a stand-in stores the raw
    message as a step, and later messages keep being redrafted - the pattern the live run showed."""
    old = re.compile(r"\b(round ?1|my (own )?(test case|steps|design|test data)|as (i )?(designed|wrote)|tc[- ]?\d+|"
                     r"(the |this )(selected |chosen )?test case)\b", re.I)
    monkeypatch.setattr(rt, "_OWN_DESIGN_RE", old)
    calls, _, conversation = _replay(monkeypatch)
    assert calls[0] == 3 and [s["step"] for s in conversation[0]["steps"]] == [MSGS[0]]   # the raw message became "the steps"
    assert sum(calls) > 5


# ---- D0 ----

@pytest.fixture
def claude(tmp_path, monkeypatch):
    """A fake Claude through the real gateway (spend, limits, the call log). Replies are JSON dicts or raw text."""
    path = tmp_path / "calls.jsonl"
    for name, value in (("ai_call_log_path", str(path)), ("claude_model", "claude-sonnet-4-5"), ("llm_fake_mode", False),
                        ("llm_tool_output", False), ("ai_reuse_replies", False), ("ai_batch_jobs", False),
                        ("ai_daily_limit_usd", 1000.0), ("ai_monthly_limit_usd", 1000.0), ("ai_candidate_round_limit_usd", 1000.0)):
        monkeypatch.setattr(llm_service.settings, name, value)
    monkeypatch.setattr(llm_service, "_MONTH_SPEND", {"month": None, "usd": 0.0})
    monkeypatch.setattr(llm_service, "_SPEND", {"day": None, "day_usd": 0.0, "candidates": {}})
    script, sent = [], []

    def create(**kwargs):
        sent.append(kwargs)
        text = script.pop(0) if len(script) > 1 else script[0]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0))
    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    client.with_options = lambda **k: client
    monkeypatch.setattr(llm_service, "_get_client", lambda: client)
    say = lambda *replies: script.extend(r if isinstance(r, str) else json.dumps(r) for r in replies)
    rows = lambda: [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return SimpleNamespace(say=say, script=script, sent=sent, rows=rows,
                           calls=lambda: [r for r in rows() if llm_service._is_call(r)],
                           checks=lambda: [r for r in rows() if r["outcome"] == "validation"])


def _turn(prompt, request_id="r1", conversation=(), design=None):
    with llm_service.call_context(round_number=2, submission_id=5, user_id=9, request_id=request_id):
        return rt.turn("python", design or {"title": "Sign in"}, list(conversation), "", prompt)


def _replay_real(claude, pattern=None, monkeypatch=None):
    """The 5 real messages through the gateway; with the old reference pattern every draft is rejected."""
    if pattern is not None:
        monkeypatch.setattr(rt, "_OWN_DESIGN_RE", pattern)
    conversation, code, outs = [], "", []
    for turn_, draft in zip(SMOKE["turns"], DRAFTS):
        claude.script.clear()
        claude.say(draft)
        with llm_service.call_context(round_number=2, submission_id=5, user_id=9, request_id=turn_["request_id"]):
            out = rt.turn(SMOKE["language"], DESIGN, conversation, code, turn_["message"], app_reference=REF)
        outs.append(out)
        conversation.append({"candidate_prompt": turn_["message"], "response_message": out["response_message"],
                             "response_kind": out["response_kind"], "steps": out["steps"]})
        code = out.get("code_after") or code
    return outs


OLD_RE = re.compile(r"\b(round ?1|my (own )?(test case|steps|design|test data)|as (i )?(designed|wrote)|tc[- ]?\d+|"
                    r"(the |this )(selected |chosen )?test case)\b", re.I)


def _no_text_of(records, texts):
    blob = json.dumps(records, ensure_ascii=False).lower()
    return [t for t in texts if t.lower() in blob]


def test_no_candidate_text_reaches_the_records_of_the_real_messages(claude, monkeypatch):
    _replay_real(claude, OLD_RE, monkeypatch)                               # 15 rejected drafts, all rules with offending items
    checks = claude.checks()
    assert len(checks) == 15 and {c["verdict"] for c in checks} == {"rejected"}
    lines = [line.strip() for t in SMOKE["turns"] for line in t["message"].splitlines() if len(line.strip()) > 3]
    assert _no_text_of(checks, lines + ["CUST001", "Pass@123", "<form", "login-form", "automate thet", "Check if they only"]) == []
    first = checks[0]
    assert first["rules"] == ["invented"] and first["reasons"]["invented"] == {"count": 1, "fingerprints": [
        llm_service.fingerprint("Login to the app successfully")]}                  # which item - without the item


def test_passwords_html_code_and_urls_never_reach_a_record(claude):
    claude.say({"reply": "Noted.", "steps": S("Log in"), "code": None})
    _turn("Log in")
    [call] = claude.calls()
    secrets = ["Pass@123", "hunter2!", '<form id="login-form" action="/ui/login">', "driver.find_element(By.ID, 'password')",
               "https://bank.example.com/login?token=abc123", "Customer id - CUST001"]
    llm_service.record_validation({"call_id": call["call_id"], "call_type": "first_draft"}, "rejected",
                                  {"retyped": secrets[:2], "dropped": secrets[2:], "compile": "  File x, line 1\n    " + secrets[3],
                                   "no_code_when_asked": True})
    assert [c["verdict"] for c in claude.checks()] == ["accepted", "rejected"]      # the turn's own, then this one
    check = claude.checks()[-1]
    assert _no_text_of(check, secrets + ["File x"]) == []
    assert check["reasons"]["retyped"]["count"] == 2 and check["reasons"]["dropped"]["count"] == 4
    assert check["reasons"]["no_code_when_asked"] == {"count": 1} and check["detail"] == "compile, dropped, no_code_when_asked, retyped"


def test_fingerprints_are_deterministic_keyed_and_ignore_spacing(monkeypatch):
    import hashlib
    one = llm_service.fingerprint("Password - Pass@123")
    assert one == llm_service.fingerprint("Password  -  Pass@123 ") and len(one) == 16
    assert one != llm_service.fingerprint("Password - Pass@124")
    assert one not in hashlib.sha256(b"Password - Pass@123").hexdigest()           # not a plain hash anyone can guess
    monkeypatch.setattr(llm_service.settings, "jwt_secret_key", "another server")
    assert llm_service.fingerprint("Password - Pass@123") != one


def test_each_verdict_names_the_exact_call_it_judged(claude):
    claude.say({"reply": "Should the booking be BK-009?", "steps": S("Log in as jordan"), "code": None},
               {"reply": "Noted: log in as jordan.", "steps": S("Log in as jordan"), "code": None})
    _turn("Log in as jordan")
    calls, checks = claude.calls(), claude.checks()
    assert [c["call_id"] for c in calls] == [v["call_id"] for v in checks]
    assert [(v["verdict"], v["call_type"], v["rules"]) for v in checks] == [("rejected", "first_draft", ["named_in_reply"]),
                                                                            ("accepted", "full_redraft", [])]
    assert checks[0]["reasons"]["named_in_reply"] == {"count": 1, "fingerprints": [llm_service.fingerprint("BK-009")]}
    assert {v["request_id"] for v in checks} == {"r1"} and all(v["cost_usd"] == 0.0 for v in checks)


def test_after_a_json_retry_the_verdict_names_the_retry(claude):
    claude.say("not json at all", {"reply": "Noted: log in as jordan.", "steps": S("Log in as jordan"), "code": None})
    _turn("Log in as jordan")
    calls = claude.calls()
    [check] = claude.checks()
    assert [c["call_type"] for c in calls] == ["first_draft", "json_retry"]
    assert (check["call_id"], check["call_type"]) == (calls[1]["call_id"], "json_retry")   # the reply that was judged


def test_a_compile_failure_names_the_call_that_wrote_the_program_not_the_latest(claude):
    """Draft 1 wrote the (broken) program; drafts 2 and 3 wrote none. The program that goes out - and fails to
    compile - is draft 1's, so the compile verdict must name draft 1 even though draft 3 was the latest call."""
    broken = "def f(:\n    print('Log in as jordan')\n"
    claude.say({"reply": "Should the booking be BK-009?", "steps": S("Log in as jordan"), "code": broken},
               {"reply": "Here it is.", "steps": S("Log in as jordan"), "code": None},
               {"reply": "Here it is.", "steps": S("Log in as jordan"), "code": None},
               {"reply": "Here it is.", "steps": S("Log in as jordan"), "code": "print('Log in as jordan')\n"})
    _turn("Log in as jordan. Generate the code.")
    calls, checks = claude.calls(), claude.checks()
    assert [c["call_type"] for c in calls] == ["first_draft", "full_redraft", "full_redraft", "compile_fix"]
    [failed] = [v for v in checks if v["verdict"] == "compile_failed"]
    assert (failed["call_id"], failed["call_type"]) == (calls[0]["call_id"], "first_draft")
    assert failed["reasons"]["compile"]["count"] == 1 and "def f" not in json.dumps(failed)
    assert (checks[-1]["call_id"], checks[-1]["verdict"]) == (calls[3]["call_id"], "accepted")


def test_no_answer_no_record(claude, monkeypatch):
    llm_service.record_validation({"call_id": None, "call_type": None}, "rejected", {"dropped": ["x"]})
    llm_service.record_validation(None, "rejected", {"dropped": ["x"]})
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_calls", 0)       # the call is blocked, never answered
    with pytest.raises(llm_service.AILimitReached):
        _turn("Log in as jordan")
    assert claude.checks() == [] and [r["outcome"] for r in claude.rows()] == ["limit_reached"]


def test_verdicts_are_not_calls_costs_or_limits(claude, monkeypatch):
    _replay_real(claude, OLD_RE, monkeypatch)
    calls, checks = claude.calls(), claude.checks()
    assert len(calls) == len(claude.sent) == len(checks) == 15
    assert [llm_service.request_totals(t["request_id"])["calls"] for t in SMOKE["turns"]] == [3, 3, 3, 3, 3]
    spent, count = llm_service._SPEND["candidates"][(9, 2, 5)]
    assert count == 15 and spent == pytest.approx(sum(c["cost_usd"] for c in calls))
    assert llm_service.month_spent_usd() == pytest.approx(sum(c["cost_usd"] for c in calls))
    assert sum(1 for r in claude.rows() if llm_service._is_call(r)) == 15             # HR's AI-health "calls" counts this way
    assert not any(r.get("outcome") == "validation" for r in llm_service._CALL_LOG)    # nor in the recent-calls list


@pytest.mark.parametrize("old", [False, True])
def test_the_verdict_records_change_no_decision(claude, monkeypatch, old):
    """Same replies, kinds, steps, code and calls with D0 switched off."""
    with_d0 = _replay_real(claude, OLD_RE if old else None, monkeypatch)
    sent = len(claude.sent)
    claude.sent.clear()
    monkeypatch.setattr(llm_service, "record_validation", lambda *a, **k: None)
    without = _replay_real(claude, OLD_RE if old else None, monkeypatch)
    strip = lambda outs: [{k: o[k] for k in ("response_kind", "response_message", "steps", "code_after")} for o in outs]
    assert strip(with_d0) == strip(without) and sent == len(claude.sent) == (15 if old else 5)


def test_a_call_that_never_answered_names_no_call(claude, monkeypatch):
    """Cut off twice, or blocked: records are written, but none of them is an answer."""
    claude.script.extend([json.dumps({"reply": "part"})])
    create = llm_service._get_client().messages.create
    cut = lambda **k: SimpleNamespace(**{**vars(create(**k)), "stop_reason": "max_tokens"})
    monkeypatch.setattr(llm_service, "_get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=cut), with_options=lambda **k: None))
    monkeypatch.setattr(llm_service, "_client_and_timeout", lambda client, budget: (client, 60))
    with llm_service.answered_call() as answered, pytest.raises(llm_service.LLMReplyTruncated):
        llm_service._call_claude_json("p", max_tokens=llm_service._MAX_OUTPUT_TOKENS // 2)
    assert [r["outcome"] for r in claude.rows()] == ["cut_off_retrying", "cut_off"] and answered["call_id"] is None
    monkeypatch.setattr(llm_service.settings, "ai_candidate_round_calls", 0)
    with llm_service.call_context(round_number=2, user_id=9), llm_service.answered_call() as answered, \
            pytest.raises(llm_service.AILimitReached):
        llm_service._call_claude_json("p")
    assert claude.rows()[-1]["outcome"] == "limit_reached" and answered["call_id"] is None
