"""
The Round 2 typing assistant's code-enforced rules (services/round2_typist.py),
with a scripted model - it knows nothing about the app, writes code only when
asked, never names what the candidate hasn't said, never repeats itself, and
a test with gaps is never "passed". No AI calls.
"""

import pytest

from app.services import llm_service, round2_typist

DESIGN = {"title": "Successful login", "steps": "Enter email priya@library.test and password Pass@123, click Sign in",
          "expected_result": "Home page shows Welcome, Priya"}


class Model:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt, max_tokens=None, schema=None):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]  # the last reply repeats


def _turn(monkeypatch, model, prompt, conversation=()):
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    return round2_typist.turn("python", DESIGN, list(conversation), "", prompt)


def test_the_model_sees_the_apps_reference_but_never_the_answer_key(monkeypatch):
    """Owner and CTO (2026-09-28): only the idea matters - the assistant finds each step in the app by the
    candidate's words, so it sees what the candidate's Reference shows; never the app's messages or data."""
    import json as _json
    from pathlib import Path as _Path
    from app.services.practice_engine import reference
    spec = _json.loads((_Path(__file__).parent / "fixtures" / "practice_engine" / "library_spec.json").read_text())
    model = Model({"reply": "Got it, the user is Priya. What next?", "code": None})
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    round2_typist.turn("python", DESIGN, [], "", "Log in as Priya", app_reference=reference.assistant_reference(reference.reference_panel(spec)))
    prompt = model.prompts[0]
    assert "PRACTICE_APP_URL" in prompt and "/api/login" in prompt and 'labelled "Email"' in prompt  # the Reference
    assert "No copies available" not in prompt and "BK-002" not in prompt  # no messages, no data rows


def test_no_code_unless_the_candidate_asks(monkeypatch):
    model = Model({"reply": "Got it, the user is Priya. Anything else, or shall I generate the code?", "code": "print(1)"})
    out = _turn(monkeypatch, model, "Log in as Priya")
    assert out["response_kind"] == "clarify" and out["code_after"] is None


@pytest.mark.parametrize("prompt, last, expected", [
    ("Generate it", "", True),
    ("please write the code now", "", True),
    ("yes", "Anything else, or shall I generate the code?", True),
    ("yes", "Got it, the user is Priya.", False),
    ("Log in as Priya", "", False),
])
def test_what_counts_as_asking_for_code(prompt, last, expected):
    conversation = [{"candidate_prompt": "x", "response_message": last}] if last else []
    assert round2_typist.wants_code(prompt, conversation) is expected


def test_code_naming_what_the_candidate_never_said_is_redrafted_then_withheld(monkeypatch):
    sneaky = {"reply": "Here's the code.", "code": 'post(PRACTICE_API_URL + "login", {"email": "x", "password": "Pass@123"})\nassert status == 200'}
    model = Model(sneaky, sneaky)
    out = _turn(monkeypatch, model, "Log in as Priya and generate the code")
    assert out["code_after"] is None and out["response_kind"] == "clarify"
    assert "200" in model.prompts[1] and "never said" in model.prompts[1]  # the redraft was told what to drop


def test_a_clean_redraft_is_used(monkeypatch):
    sneaky = {"reply": "Done.", "code": 'send("/api/login")'}
    clean = {"reply": "Here's the code for what you described.", "code": 'incomplete("log in as Priya")'}
    out = _turn(monkeypatch, Model(sneaky, clean), "Log in as Priya. Generate the code.")
    assert out["response_kind"] == "code_edit" and out["code_after"] == 'incomplete("log in as Priya")'


def test_replies_never_name_things_either(monkeypatch):
    out = _turn(monkeypatch, Model({"reply": "Should I POST to /api/login and expect 200?", "code": None},
                                   {"reply": "Which address should it use?", "code": None}), "Log in as Priya through the API")
    assert "/api/login" not in out["response_message"] and "200" not in out["response_message"]


def test_the_candidates_own_words_and_round_1_steps_are_allowed(monkeypatch):
    code = 'driver.find_element(By.ID, "email").send_keys("priya@library.test")\nassert status == 200'
    model = Model({"reply": "Here it is.", "code": code})
    out = _turn(monkeypatch, model, "Use my Round 1 steps, the email field id is email, expect status 200. Generate it.")
    assert out["code_after"] == code


def test_a_repeated_reply_is_replaced(monkeypatch):
    said = "Got it, the user is Priya. Anything else, or shall I generate the code?"
    conversation = [{"candidate_prompt": "Log in as Priya", "response_message": said}]
    out = _turn(monkeypatch, Model({"reply": said, "code": None}, {"reply": said, "code": None}), "hmm", conversation)
    assert out["response_message"] != said


def test_requests_to_decide_or_invent_are_refused_without_the_model(monkeypatch):
    model = Model()
    out = _turn(monkeypatch, model, "Add more test cases and decide the assertions for me")
    assert out["response_kind"] == "refuse" and model.prompts == []


@pytest.mark.parametrize("exit_code, stdout, timed_out, infra, status", [
    (0, "PASS login\n", False, False, "passed"),
    (1, "FAIL login\n", False, False, "failed"),
    (3, "INCOMPLETE: log in as Priya\n", False, False, "incomplete"),
    (0, "INCOMPLETE: check it works\n", False, False, "incomplete"),  # a test with gaps is never passed
    (None, "", True, False, "timed_out"),
    (None, "", False, True, "error"),
])
def test_run_status(exit_code, stdout, timed_out, infra, status):
    assert round2_typist.run_status(exit_code, stdout, timed_out, infra) == status


def test_the_prompt_template_formats():
    text = llm_service._load_prompt("round2_typist_turn.txt").format(language="java", conventions="c", design="d", current_code="",
                                                                   conversation="", candidate_prompt="p", app_reference="a", steps_so_far="s")
    assert '{"reply"' in text and "<<CACHE_BREAK>>" in text


def test_apostrophes_in_prose_are_not_quotes():
    """Measured: "Got it, you're asking what Priya's password is. I don't know that" - a correct reply - was blocked."""
    said = round2_typist.candidate_text(DESIGN, [{"candidate_prompt": "Log in as Priya.", "response_message": "x"}], "What's Priya's password?")
    reply = "Got it, you're asking what Priya's password is. I don't know that - I need you to tell me. What password should I use for Priya?"
    assert round2_typist.unsaid(reply, said, code=False) == []
    # a real quoted name the candidate never said is still caught
    assert "Sign" in round2_typist.unsaid('Should I use the "Sign in" button?', said, code=False)


def test_a_waits_time_limit_is_not_an_invented_value():
    """Measured: WebDriverWait(driver, 10) withheld a strong candidate's code."""
    said = "Open PRACTICE_APP_URL, click id login, wait until the page title contains Home. Generate it."
    code = 'wait = WebDriverWait(driver, 10)\nwait.until(EC.title_contains("Home"))\ndriver.find_element(By.ID, "login").click()'
    assert round2_typist.unsaid(code, said, code=True) == []
    assert round2_typist.unsaid(code + "\nassert total == 3537", said, code=True) == ["3537"]  # other numbers still checked


def test_what_a_program_prints_is_not_checked_as_app_knowledge():
    """Measured: print/log text in the code (got {actual_name}, HTTP Error) withheld a strong candidate's code."""
    said = "POST to PRACTICE_API_URL + login with JSON body email priya@library.test and password Pass@123. Expect status 200 and name is Priya."
    code = ('body = json.dumps({"email": "priya@library.test", "password": "Pass@123"})\n'
            'print(f"FAIL: expected name Priya, got {actual_name}")\nprint("HTTP Error", err)\nassert status == 200')
    assert round2_typist.unsaid(code, said, code=True) == []
    assert "/api/signin" in round2_typist.unsaid(code + '\nurl = "/api/signin"', said, code=True)  # app details still checked


def test_a_reply_may_ask_which_element_but_never_guess_the_app_or_invent_values():
    """Agreed design (2026-09-28): the assistant sees the Reference, so asking "which page / which button?"
    is how it asks for a detail - blocking those words withheld 14 of 26 correct drafts in the owner's
    Round 2. What it may never do is name a part of the app that is neither the candidate's nor on the
    Reference (a guess), or bring up a value the candidate never gave (a hint)."""
    said = "Automate TC-01. Log in as Priya. Just check it works. Generate it."
    assert round2_typist.unsaid("I need to know: what's the URL or page, and which button?", said, code=False) == []
    assert round2_typist.unsaid("Got it, you want me to log in as Priya and check it works.", said, code=False) == []
    assert "Dashboard page" in round2_typist.unsaid("Should I start from the Dashboard page?", said, code=False)
    assert "BK-009" in round2_typist.unsaid("Should the booking be BK-009?", said, code=False)
    reference = "PAGE Dashboard (/page/dashboard):\n  button text \"Log in\" id=login"
    assert round2_typist.unsaid("I found the Dashboard page and the login button.", said + "\n" + reference, code=False) == []


def test_when_asked_for_code_a_reply_without_code_is_asked_again(monkeypatch):
    first = {"reply": "Got it. Anything else?", "code": None}
    second = {"reply": "Here is the code for what you said.", "code": 'incomplete("log in as Priya")'}
    out = _turn(monkeypatch, Model(first, second), "Log in as Priya. Generate it.")
    assert out["response_kind"] == "code_edit" and out["code_after"] == 'incomplete("log in as Priya")'


def test_withheld_code_never_leaves_a_reply_promising_code(monkeypatch):
    """Real Java check (2026-09-27): a weak candidate said "Generate it." with no element
    ids; the model guessed locators both times, the code was rightly withheld - but its
    reply "I'll write the code now" still went out, promising code that never came."""
    guessed = {"reply": "Got it, you're asking me to generate it. I'll write the code now.",
               "code": 'driver.findElement(By.id("username")).sendKeys("priya@library.test");'}
    model = Model(guessed, guessed)
    out = _turn(monkeypatch, model, "Generate it.", [{"candidate_prompt": "Automate TC-01.", "response_message": "Got it."}])
    assert out["code_after"] is None
    assert "write the code now" not in out["response_message"]
    assert out["response_message"] in round2_typist._BLOCKED
    assert "incomplete(" in model.prompts[1]  # the retry was told to mark the unsaid steps incomplete


def test_a_comment_is_not_a_path_but_a_path_in_a_comment_still_is():
    """Persona browser test (2026-09-27): "//" was read as an unsaid path, so every
    Java/JavaScript program with a comment - even the untouched starter file - was withheld."""
    said = "Sign in and check the name. Generate the code."
    for language, starter in round2_typist.STARTERS.items():
        assert round2_typist.unsaid(starter, said, code=True) == [], language
    assert round2_typist.unsaid("// sign in\nx = 1;", said, code=True) == []
    assert "/api/login" in round2_typist.unsaid("// call /api/login\nx = 1;", said, code=True)


def test_code_with_gaps_tells_the_candidate_which_of_their_steps_need_a_how(monkeypatch):
    """The owner's Round 2 (2026-09-27): code full of incomplete() and no word why."""
    code = 'driver.get(System.getenv("PRACTICE_APP_URL"));\nincomplete("Locate Customer id and enter CUST001");\nincomplete("Click on Log in button");'
    out = _turn(monkeypatch, Model({"reply": "Here's the code.", "code": code}),
                "Open PRACTICE_APP_URL. Locate Customer id and enter CUST001. Click on Log in button. Generate the code.")
    assert out["code_after"] == code
    msg = out["response_message"]
    assert round2_typist._TODO_NOTE in msg and "1. Locate Customer id and enter CUST001" in msg and "2. Click on Log in button" in msg


def test_a_field_found_by_the_label_the_candidate_named_is_not_withheld(monkeypatch):
    code = ('driver.findElement(By.xpath("//label[normalize-space()=\'Customer id\']/following::input[1]")).sendKeys("CUST001");\n'
            'driver.findElement(By.xpath("//button[normalize-space()=\'Log in\']")).click();')
    out = _turn(monkeypatch, Model({"reply": "Done.", "code": code}),
                "Enter CUST001 in the field labelled Customer id and click the Log in button. Generate the code.")
    assert out["code_after"] == code
    guessed = 'driver.findElement(By.xpath("//input[@id=\'cust_id\']")).sendKeys("CUST001");'
    out = _turn(monkeypatch, Model({"reply": "Done.", "code": guessed}, {"reply": "Done.", "code": guessed}),
                "Enter CUST001 in the field labelled Customer id. Generate the code.")
    assert out["code_after"] is None  # an id they never gave is still a guess



def test_a_third_plainest_try_when_the_code_keeps_guessing(monkeypatch):
    """Realistic check (2026-09-27): asked to click "the Log in button", the model found it by a guessed
    type="submit" twice and the candidate was left with a vague "I need more". A third try is told plainly
    to find elements only by what the candidate gave; a label's `for` attribute is not a guess."""
    guess = {"reply": "Done.", "code": 'driver.findElement(By.cssSelector("button[type=\'submit\']")).click();'}
    good = {"reply": "Done.", "code": 'driver.findElement(By.xpath("//button[contains(text(), \'Log in\')]")).click();'}
    model = Model(guess, guess, good)
    out = _turn(monkeypatch, model, "Click the Log in button. Generate the code.")
    assert out["code_after"] == good["code"]
    assert "ONLY by what the candidate gave" in model.prompts[2]
    label = 'driver.findElement(By.xpath("//input[@id=(//label[contains(text(), \'Customer id\')]/@for)]")).sendKeys("CUST001");'
    assert round2_typist.unsaid(label, "Enter CUST001 in the field labelled Customer id.", code=True) == []


BROKEN = 'import sys\nlogin_button = find(By.XPATH, "//button[contains(text(), \'Log in\')]"))\n'
FIXED = 'import sys\nlogin_button = find(By.XPATH, "//button[contains(text(), \'Log in\')]")\n'
SAID_LOGIN = "Click on Log in button. Generate the code."


def test_code_that_does_not_compile_is_fixed_before_the_candidate_sees_it(monkeypatch):
    """Owner's Round 2 (2026-09-28): the assistant's code had an extra ")" and the candidate got a SyntaxError."""
    model = Model({"reply": "Here it is.", "code": BROKEN}, {"reply": "Here it is.", "code": FIXED})
    out = _turn(monkeypatch, model, SAID_LOGIN)
    assert out["code_after"] == FIXED and round2_typist.compile_problem("python", out["code_after"]) is None
    assert "doesn't compile" in model.prompts[-1] and "unmatched" in model.prompts[-1]


def test_fix_the_syntax_error_rewrites_the_code(monkeypatch):
    """The owner asked "Fix the syntax error" - it changed nothing."""
    model = Model({"reply": "Fixed the extra parenthesis.", "code": FIXED})
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    out = round2_typist.turn("python", DESIGN, [{"candidate_prompt": SAID_LOGIN, "response_message": "Here it is."}], BROKEN,
                             "Fix the syntax error")
    assert out["response_kind"] == "code_edit" and out["code_after"] == FIXED
    assert "fix a syntax/compile error" in model.prompts[0] and "unmatched" in model.prompts[0]
    out = round2_typist.turn("python", DESIGN, [{"candidate_prompt": SAID_LOGIN, "response_message": "Here it is."}], BROKEN,
                             "fix the error on line 2")  # no "syntax" - but the current code doesn't compile
    assert out["code_after"] == FIXED


def test_it_never_claims_a_change_it_did_not_make(monkeypatch):
    """Asked "is the syntax error fixed?", it said "Yes, the syntax error is fixed" - with no new code."""
    model = Model({"reply": "Yes, the syntax error is fixed - I removed the extra parenthesis.", "code": None})
    monkeypatch.setattr(llm_service, "_call_claude_json", model)
    out = round2_typist.turn("python", DESIGN, [{"candidate_prompt": SAID_LOGIN, "response_message": "Here it is."}], FIXED,
                             "is the syntax error fixed?")
    assert out["code_after"] is None
    assert "fixed" not in out["response_message"].lower() or "haven't changed" in out["response_message"]
    assert out["response_message"] in round2_typist._NO_CHANGE


def test_the_selected_test_case_counts_as_the_candidates_own_design():
    assert round2_typist._OWN_DESIGN_RE.search("automate the selected test case")


# ---- Owner's Round 2, Loan EMI, 2026-09-28: four "What's next for this test?" in a row, and no code ----

def test_when_no_draft_can_go_out_the_reply_is_built_from_the_candidates_words(monkeypatch):
    """Never a bare "What's next?": it read as the assistant ignoring what was said."""
    leak = {"reply": "Should the booking be BK-009?", "code": None}
    out = _turn(monkeypatch, Model(leak), "Locate Outstanding balance and verify it shows 125000.00")
    assert "Locate Outstanding balance and verify it shows 125000.00" in out["response_message"]
    assert "BK-009" not in out["response_message"] and "generate" in out["response_message"]
    # ... and it offers the code, so a "yes" next counts as asking for it
    assert round2_typist.wants_code("yes", [{"candidate_prompt": "x", "response_message": out["response_message"]}])


def test_the_required_not_enough_details_sentence_may_be_said_again(monkeypatch):
    """The rules make it say this whenever it is true; the old sentence check blocked the second time."""
    earlier = [{"candidate_prompt": "Log in", "response_message": "Noted: log in. I don't have enough details to generate the code yet."}]
    reply = {"reply": "You want to click EMI Details. I don't have enough details to generate the code yet.", "code": None}
    out = _turn(monkeypatch, Model(reply), "Click EMI Details", earlier)
    assert out["response_message"] == reply["reply"]
    same = {"reply": earlier[0]["response_message"], "code": None}  # the whole reply again is still a repeat
    assert _turn(monkeypatch, Model(same), "Click EMI Details", earlier)["response_message"] != same["reply"]


def test_step_numbers_exit_codes_and_python_main_are_not_application_values():
    said = "Enter SAV-1001, check Outstanding balance shows 125000.00"
    code = ('# Step 4: enter the account\nfield.send_keys("SAV-1001")\nsys.exit(1)\nsteps[6]\n'
            'if actual != "125000.00":\n    fail(7)\nif __name__ == "__main__":\n    main()\n')
    assert round2_typist.unsaid(code, said, code=True) == []
    assert "99999.50" in round2_typist.unsaid(code + "expected = 99999.50\n", said, code=True)  # a real value still caught


def test_logging_in_with_no_account_named_may_use_the_test_account(monkeypatch):
    """Agreed with the owner: "After login" means the Reference's test account, and the reply says so."""
    ref = "Test accounts (as the Reference shows them): login CUST001 / password Pass@123 (Test User)"
    reply = {"reply": "For 'After login' I'll use the test account CUST001 / Pass@123. What next?", "code": None}
    monkeypatch.setattr(llm_service, "_call_claude_json", Model(reply))
    out = round2_typist.turn("python", DESIGN, [], "", "After login, click EMI Details", app_reference=ref)
    assert out["response_message"] == reply["reply"]
    monkeypatch.setattr(llm_service, "_call_claude_json", Model(reply))
    out = round2_typist.turn("python", DESIGN, [], "", "Click EMI Details", app_reference=ref)
    assert "CUST001" not in out["response_message"]  # no login mentioned: the account is still a hint


@pytest.mark.parametrize("reply", ["Perhaps wait for the page to load?", "Should I also check the total?",
                                   "You could add a wait after login.", "Which page or API response should it check?"])
def test_offering_a_step_or_hinting_the_api_is_still_blocked(reply):
    assert round2_typist.unsaid(reply, "Log in and click EMI Details", code=False)


def test_a_variable_inside_code_text_is_not_an_application_value():
    said = "After login click EMI Details"
    code = 'driver.get(f"{practice_url}/page/login")\nfetch(`${baseUrl}/api/loans`)\n'
    assert round2_typist.unsaid(code, said + "\n/page/login /api/loans", code=True) == []
    assert "LN-45678" in round2_typist.unsaid(code + 'x = f"{a} LN-45678"\n', said, code=True)


def test_asked_for_code_the_first_try_already_says_write_it_now(monkeypatch):
    model = Model({"reply": "Here it is.", "code": 'incomplete("click EMI Details")'})
    _turn(monkeypatch, model, "Click EMI Details. Generate the code.")
    assert round2_typist._WRITE_NOTE in model.prompts[0]


# ---- The candidate's test is kept by the tool (owner's Round 2 replay, 2026-09-28: the model offered
# "should I skip those steps?", took the next "yes" as agreement and two steps vanished from the code) ----

PRIOR = [{"step": "After login", "missing": ""}, {"step": "Locate EMI Details and click", "missing": ""},
         {"step": "Locate loan account and enter his account details", "missing": "which value"}]
SO_FAR = [{"candidate_prompt": "After login. Locate EMI Details and click. Locate loan account and enter his account details",
           "response_message": "Noted.", "steps": PRIOR}]


def test_a_step_the_model_drops_is_redrafted_and_never_lost(monkeypatch):
    dropping = {"reply": "Now I have: login, click EMI Details, check the balance.",
                "steps": [PRIOR[0], PRIOR[1], {"step": "Locate Outstanding balance and verify 125000.00"}], "code": None}
    keeping = {"reply": "Added your check at the end.",
               "steps": PRIOR + [{"step": "Locate Outstanding balance and verify 125000.00", "missing": ""}], "code": None}
    model = Model(dropping, keeping)
    out = _turn(monkeypatch, model, "Locate Outstanding balance and verify 125000.00", SO_FAR)
    assert out["response_message"] == keeping["reply"] and len(out["steps"]) == 4
    assert "left out steps" in model.prompts[1] and "loan account" in model.prompts[1]
    # a model that keeps dropping it: the tool keeps their steps anyway, and adds the new one
    out = _turn(monkeypatch, Model(dropping), "Locate Outstanding balance and verify 125000.00", SO_FAR)
    assert [s["step"] for s in out["steps"]][:3] == [s["step"] for s in PRIOR] and len(out["steps"]) == 4


def test_the_candidate_may_remove_a_step(monkeypatch):
    fewer = {"reply": "Removed the loan account step.", "steps": PRIOR[:2], "code": None}
    out = _turn(monkeypatch, Model(fewer), "Remove the loan account step", SO_FAR)
    assert out["response_message"] == fewer["reply"] and len(out["steps"]) == 2


def test_code_that_leaves_out_a_step_of_theirs_never_reaches_them(monkeypatch):
    code = 'login()\nprint("FAIL: step 2 - Locate EMI Details and click")\n'  # no loan account step
    full = code + 'incomplete("Locate loan account and enter his account details")\n'
    model = Model({"reply": "Here it is.", "steps": PRIOR, "code": code}, {"reply": "Here it is.", "steps": PRIOR, "code": full})
    out = _turn(monkeypatch, model, "Generate the code", SO_FAR)
    assert out["code_after"] == full


@pytest.mark.parametrize("reply", ["Or should I skip those steps and just do the login?", "Should I drop step 3?",
                                   "Should I remove the EMI Payment steps?"])
def test_offering_to_skip_or_drop_their_steps_is_blocked(reply):
    assert round2_typist.unsaid(reply, "After login. Locate EMI Payment and click", code=False)


def test_yes_to_not_enough_details_is_not_asking_for_code():
    """Replay 2026-09-28: "I don't have enough details to generate the code yet" read as an offer."""
    said = [{"candidate_prompt": "x", "response_message": "Noted. I don't have enough details to generate the code yet - which value?"}]
    assert not round2_typist.wants_code("yes", said)
    offer = [{"candidate_prompt": "x", "response_message": "I have everything. Shall I generate the code now?"}]
    assert round2_typist.wants_code("yes", offer)


# ---- Simulated candidates, Java, 2026-09-28 ----

def test_yes_followed_by_a_new_step_is_not_asking_for_code():
    offer = [{"candidate_prompt": "x", "response_message": "Shall I generate the code for this step now?"}]
    assert not round2_typist.wants_code("Yes, continue. Next step: Log in using the test account CUST001 / Pass@123.", offer)
    assert round2_typist.wants_code("yes please", offer) and round2_typist.wants_code("ok, go ahead", offer)


@pytest.mark.parametrize("said, own, code, missing", [
    ("Locate Password and enter Password", None, 'send("Pass@123")', ["Password"]),        # the owner's rule
    ("Locate Password and enter Password", None, 'send("Password")', []),
    ("Locate Customer id and enter CUST001", None, 'send("CUST01")', ["CUST001"]),
    ("Enter SAV-1001 in the account field", None, 'send("LN-45678")', ["SAV-1001"]),
    ("Locate loan account and enter his account details", None, 'send("x")', []),        # not a value
    ("Locate Password and enter Password. use the test account password from the reference", None, 'send("Pass@123")', []),
    # Round 1 said "enter Password", the conversation said Pass@123: their latest words win
    ("Locate Password and enter Password\nEnter Pass@123 in the password field", "Enter Pass@123 in the password field",
     'send("Pass@123")', []),
])
def test_values_the_candidate_typed_are_typed_as_written(said, own, code, missing):
    assert round2_typist._values_not_typed(said, code, own) == missing


def test_code_that_changes_a_typed_value_is_redrafted(monkeypatch):
    wrong = {"reply": "Here it is.", "steps": [{"step": "Locate Password and enter Password", "missing": ""}],
             "code": 'password.send_keys("Pass@123")\n# Locate Password and enter Password\n'}
    right = dict(wrong, code='password.send_keys("Password")\n# Locate Password and enter Password\n')
    model = Model(wrong, right)
    out = _turn(monkeypatch, model, "Locate Password and enter Password. Generate the code.")
    assert out["code_after"] == right["code"] and "Password" in model.prompts[1].split("changed:")[1]


def test_a_new_step_nobody_gave_is_redrafted(monkeypatch):
    s1 = [{"step": "Click EMI Details", "missing": ""}]
    extra = {"reply": "Noted.", "steps": s1 + [{"step": "Verify the application header shows the bank name", "missing": ""}], "code": None}
    model = Model(extra, {"reply": "Noted.", "steps": s1, "code": None})
    out = _turn(monkeypatch, model, "Click EMI Details")
    assert [s["step"] for s in out["steps"]] == ["Click EMI Details"] and "never gave" in model.prompts[1]


def test_when_code_goes_out_a_blocked_reply_never_asks_whether_to_generate(monkeypatch):
    code = 'incomplete("Just generate the code")\n'
    earlier = [{"candidate_prompt": "Generate", "response_message": "The code is ready."}]
    out = _turn(monkeypatch, Model({"reply": "The code is ready.", "code": code}), "Just generate the code.", earlier)
    assert out["code_after"] and "shall I generate" not in out["response_message"]


# ---- Simulated candidates, run 2 (Java, 12 of 12, 2026-09-28): drafts blocked for nothing ----

@pytest.mark.parametrize("reply, said", [
    ("I understand you want me to add debugging output to print the full API response.", "Add debugging output printing the API response"),
    ("You want to try a different approach: after the Loans page, enter the loan account.", "try a different approach: Loans page first, then the loan account"),
    ("Perfect! I'll generate the complete code now.", "Generate the code"),
    ("Done - PASS** lines for each check.", "print PASS for each check"),
    ("I'll add a 3-second wait after clicking.", "wait 3 seconds after clicking"),
    ("It fails on a non-200 status.", "check the status is 200"),
])
def test_what_the_simulation_found_blocked_for_nothing_now_goes_through(reply, said):
    assert round2_typist.unsaid(reply, said, code=False) == []


def test_html_tags_and_tolerances_in_code_are_plumbing():
    code = 'rows = driver.find_elements(By.CSS_SELECTOR, "tbody tr td")\nassert abs(a - b) < 0.01\n'
    assert round2_typist.unsaid(code, "check the table", code=True) == []


@pytest.mark.parametrize("reply", ["Would you like me to add a step to verify the login API returns HTTP 200 status?",
                                   "Should I also check the total?", "Perfect! The booking is BK-009."])
def test_real_offers_and_values_are_still_blocked(reply):
    assert round2_typist.unsaid(reply, "Log in via the API and check the loans", code=False)


@pytest.mark.parametrize("prompt", ["Now proceed with the code", "Please implement it", "ok, automate this"])
def test_more_ways_of_asking_for_the_code(prompt):
    assert round2_typist.wants_code(prompt, [])
    assert not round2_typist.wants_code("proceed to the payment page", [])


def test_repeating_the_request_back_as_a_question_is_not_an_offer():
    said = "Add debugging output printing the API response"
    assert round2_typist.unsaid("So you want me to add debugging output printing the API response - is that right?", said, code=False) == []
