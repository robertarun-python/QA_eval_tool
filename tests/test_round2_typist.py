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


def test_the_model_never_sees_the_application(monkeypatch):
    model = Model({"reply": "Got it, the user is Priya. What next?", "code": None})
    _turn(monkeypatch, model, "Log in as Priya")
    prompt = model.prompts[0]
    assert "PRACTICE_APP_URL" in prompt  # the project conventions only
    assert "library_spec" not in prompt and "available_copies" not in prompt and "/api/login" not in prompt


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
                                                                   conversation="", candidate_prompt="p")
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


def test_a_reply_may_not_bring_up_parts_of_the_app_the_candidate_never_mentioned():
    """Measured: "I need to know: What's the URL or page..." after a vague "Generate it"."""
    said = "Automate TC-01. Log in as Priya. Just check it works. Generate it."
    assert set(round2_typist.unsaid("I need to know: what's the URL or page, and which button?", said, code=False)) >= {"URL", "page", "button"}
    assert round2_typist.unsaid("Got it, you want me to log in as Priya and check it works.", said, code=False) == []
    said_more = "Click the login button on the login page."
    assert round2_typist.unsaid("Got it: click the login button on the login page.", said_more, code=False) == []


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
