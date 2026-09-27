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
        return self.replies.pop(0)


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
