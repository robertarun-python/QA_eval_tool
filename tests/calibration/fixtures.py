"""
Score calibration: one practice scenario per round, and three hand-written
answers to it - STRONG, AVERAGE and WEAK - whose quality is known in
advance. The scorers must rank them in that order, pass the strong one,
fail the weak one, and give the same answer much the same score twice
(see run_calibration.py).

Written from scratch for calibration: no real candidate's work and no live
scenario's answer key. The Round 2 automation tier uses the seeded
practice environment and its ground truth (seed_round2_automation.py),
which are system data, not a candidate-facing answer key.
"""
import json

# ---------------------------------------------------------------- Round 1
R1_SCENARIO = (
    "Patient portal login page: email and password fields, a 'Remember me' checkbox and a "
    "'Forgot password?' link. A successful login opens the dashboard with 'Welcome, <first name>'. "
    "After 5 consecutive wrong passwords the account is locked for 15 minutes and the page says "
    "'Account locked - try again in 15 minutes'. Both fields are required."
)
R1_REFERENCE = [
    {"title": "Valid login opens the dashboard", "preconditions": "Active account exists", "steps": "1. Open login 2. Enter valid email and password 3. Click Log in",
     "test_data": "email = jordan.rivera@example.com; password = Passw0rd!2026", "expected_result": "Dashboard opens with 'Welcome, Jordan'"},
    {"title": "Wrong password is rejected", "preconditions": "Active account exists", "steps": "1. Enter valid email, wrong password 2. Click Log in",
     "test_data": "password = wrong123", "expected_result": "Error shown, user stays on login page"},
    {"title": "Account locks after 5 wrong passwords", "preconditions": "Active account, 0 failed attempts", "steps": "1. Enter wrong password 5 times",
     "test_data": "5 x password = wrong123", "expected_result": "'Account locked - try again in 15 minutes'; 6th attempt with correct password also refused"},
    {"title": "Required fields", "preconditions": "", "steps": "1. Leave email and/or password empty 2. Click Log in",
     "test_data": "email = ''; password = ''", "expected_result": "Validation message on each empty field; no login request"},
    {"title": "Remember me keeps the session", "preconditions": "Active account", "steps": "1. Tick Remember me 2. Log in 3. Close and reopen browser",
     "test_data": "valid credentials", "expected_result": "User is still logged in"},
    {"title": "Forgot password link", "preconditions": "", "steps": "1. Click 'Forgot password?'",
     "test_data": "", "expected_result": "Password reset page opens"},
]
R1_ANSWERS = {
    "strong": [
        {"title": "Valid login shows the dashboard greeting", "preconditions": "Account jordan.rivera@example.com is active, 0 failed attempts",
         "steps": "1. Open /login\n2. Enter email and password\n3. Click Log in", "test_data": "email = jordan.rivera@example.com; password = Passw0rd!2026",
         "expected_result": "Dashboard opens and the header reads 'Welcome, Jordan'"},
        {"title": "Wrong password is rejected without revealing which field was wrong", "preconditions": "Same active account",
         "steps": "1. Enter the valid email\n2. Enter a wrong password\n3. Click Log in", "test_data": "email = jordan.rivera@example.com; password = Wrong!123",
         "expected_result": "Generic 'Incorrect email or password' message; still on /login; failed-attempt counter = 1"},
        {"title": "Lockout after exactly 5 wrong passwords", "preconditions": "Active account, 0 failed attempts",
         "steps": "1. Submit a wrong password 4 times - still allowed\n2. Submit a wrong password a 5th time\n3. Immediately try the correct password",
         "test_data": "5 x password = Wrong!123, then Passw0rd!2026", "expected_result": "After the 5th: 'Account locked - try again in 15 minutes'; the correct password is also refused while locked"},
        {"title": "Lock expires after 15 minutes", "preconditions": "Account locked at 10:00",
         "steps": "1. At 10:14 log in with the correct password\n2. At 10:15 log in again", "test_data": "password = Passw0rd!2026",
         "expected_result": "10:14 refused (still locked); 10:15 succeeds"},
        {"title": "Both fields required", "preconditions": "",
         "steps": "1. Leave both empty, click Log in\n2. Fill only email, click Log in", "test_data": "email = ''/'jordan.rivera@example.com'; password = ''",
         "expected_result": "Field-level 'required' message on each empty field; no request sent; attempt counter unchanged"},
        {"title": "Remember me persists the session across a browser restart", "preconditions": "Active account",
         "steps": "1. Tick Remember me\n2. Log in\n3. Close the browser and reopen /dashboard", "test_data": "valid credentials",
         "expected_result": "Dashboard opens without logging in again"},
        {"title": "Forgot password link opens reset", "preconditions": "", "steps": "1. Click 'Forgot password?'", "test_data": "",
         "expected_result": "Password reset page opens with an email field"},
    ],
    "average": [
        {"title": "Login with valid details", "preconditions": "User has an account", "steps": "Enter email and password and log in",
         "test_data": "valid email and password", "expected_result": "User is logged in"},
        {"title": "Login with wrong password", "preconditions": "User has an account", "steps": "Enter email and a wrong password",
         "test_data": "wrong password", "expected_result": "Error message is shown"},
        {"title": "Empty fields", "preconditions": "", "steps": "Click login without entering anything",
         "test_data": "", "expected_result": "Validation errors"},
    ],
    "weak": [
        {"title": "Login works", "preconditions": "", "steps": "Log in", "test_data": "", "expected_result": "It works"},
    ],
}

# ---------------------------------------------------------------- Round 4 (debugging; scorer is score_round2_submission)
R4_SCENARIO = (
    "Since yesterday's release, about 8% of checkout payments fail with 'Payment timed out'. Failures "
    "cluster at busy times. You have application logs, the payment gateway dashboard, database metrics "
    "and the release notes (which include 'moved payment calls to a shared HTTP connection pool')."
)
R4_REFERENCE = [
    {"title": "Scope the failure", "steps": "Compare failure rate and timing with traffic; check if specific payment methods or regions", "expected_result": "Failures correlate with load, not method"},
    {"title": "Check the gateway side", "steps": "Gateway dashboard: latency and errors for our requests", "expected_result": "Gateway healthy; many of our requests never arrive"},
    {"title": "Check the release change", "steps": "Release notes: shared HTTP connection pool; inspect pool size and wait times", "expected_result": "Pool of 10 connections exhausted at peak; requests wait until timeout"},
    {"title": "Rule out the database", "steps": "DB metrics during failures", "expected_result": "Normal"},
    {"title": "Confirm", "steps": "Raise pool size in staging / load test", "expected_result": "Timeouts disappear"},
]
R4_ANSWERS = {
    "strong": {
        "investigation": [
            {"area": "Scoped it first: failures started exactly at the release, are ~8% overall but up to 25% at the 12:00-13:00 peak, and hit every payment method and region equally - so load-related, not a specific provider or card type."},
            {"area": "Gateway dashboard: their latency and error rate are normal, and they show ~8% fewer requests from us than our logs attempted - the failing requests never reach the gateway."},
            {"area": "Release notes mention the new shared HTTP connection pool. Logs show 'waiting for connection from pool' for 29-30 s before each timeout; pool size is configured at 10."},
            {"area": "Database metrics during the failure windows: normal query times and connections - ruled out."},
            {"area": "Reproduced in staging with a load test at peak volume: timeouts appear at pool size 10 and disappear at 50."},
        ],
        "root_cause": ("The release's shared HTTP connection pool (size 10) is exhausted under peak load, so payment requests wait for a "
                       "connection until the 30 s timeout and never reach the gateway. Ruled out the gateway (healthy, fewer requests "
                       "received) and the database (normal). Confirmed by reproducing in staging and fixing it by raising the pool size."),
    },
    "average": {
        "investigation": [
            {"area": "Checked the application logs and saw many timeout errors after the release."},
            {"area": "Looked at the payment gateway dashboard, it seems fine."},
        ],
        "root_cause": "Probably the release introduced something slow in the payment call, maybe the new connection handling. Needs more investigation.",
    },
    "weak": {
        "investigation": [{"area": "Looked at logs."}],
        "root_cause": "The payment gateway is slow.",
    },
}

# ---------------------------------------------------------------- Round 3
R3_SCENARIO = (
    "Given a list of integers, write a program that finds and prints the second-largest DISTINCT value in the "
    "list (duplicates of the largest value do not count as a separate value). If the list has fewer than two "
    "distinct values, print -1 instead. Input: one line of comma-separated integers."
)
R3_APPROACH = "Remove duplicates, then find the second-largest value (e.g. track largest and second-largest in one pass); -1 when fewer than two distinct values."
_R3_INPUTS = [("3,1,4", "3"), ("5,5,5", "-1"), ("9", "-1"), ("", "-1"), ("1,2", "1"), ("7,7,3", "3"), ("-1,-5,-3", "-3"),
              ("10,20,20,10", "10"), ("100,99", "99"), ("4,4,4,2", "2"), ("0,0,1", "0"), ("2,3,3,1", "2"),
              ("-2,-2", "-1"), ("8,6,8,6", "6"), ("1,1,2,2,3,3", "2")]


def _tests(passing: int) -> list[dict]:
    return [{"input": i, "expected_output": o, "description": f"case {n + 1}",
             "actual_output": o if n < passing else "wrong", "passed": n < passing}
            for n, (i, o) in enumerate(_R3_INPUTS)]


R3_ANSWERS = {
    "strong": {
        "test_results": _tests(15),
        "conversation": [
            {"turn_number": 1, "candidate_prompt": "Read one line from input, split it on commas and convert each part to an int, storing them in a list called nums. If the line is empty, nums should be an empty list.",
             "response_kind": "code_edit", "response_message": "Done.", "code_after": "line = input().strip()\nnums = [int(x) for x in line.split(',')] if line else []\n"},
            {"turn_number": 2, "candidate_prompt": "Remove duplicates by converting nums to a set, and keep the distinct values in a list called distinct.",
             "response_kind": "code_edit", "response_message": "Done.", "code_after": "line = input().strip()\nnums = [int(x) for x in line.split(',')] if line else []\ndistinct = list(set(nums))\n"},
            {"turn_number": 3, "candidate_prompt": "Now loop over distinct once, tracking largest and second_largest (both start as None): a bigger value moves largest down to second_largest. After the loop print second_largest, or -1 if it is still None.",
             "response_kind": "code_edit", "response_message": "Done.",
             "code_after": "line = input().strip()\nnums = [int(x) for x in line.split(',')] if line else []\ndistinct = list(set(nums))\nlargest = second_largest = None\nfor v in distinct:\n    if largest is None or v > largest:\n        second_largest, largest = largest, v\n    elif second_largest is None or v > second_largest:\n        second_largest = v\nprint(second_largest if second_largest is not None else -1)\n"},
            {"turn_number": 4, "candidate_prompt": "I ran it with 5,5,5 and got -1 and with 3,1,4 got 3, which matches. Keep it as is.",
             "response_kind": "explain", "response_message": "Understood - no change.", "code_after": None},
        ],
    },
    "average": {
        "test_results": _tests(10),
        "conversation": [
            {"turn_number": 1, "candidate_prompt": "Read the numbers from input into a list.",
             "response_kind": "code_edit", "response_message": "Read one line and split on commas.", "code_after": "nums = [int(x) for x in input().split(',')]\n"},
            {"turn_number": 2, "candidate_prompt": "Sort the list from biggest to smallest and print the second one.",
             "response_kind": "code_edit", "response_message": "Done.", "code_after": "nums = [int(x) for x in input().split(',')]\nnums.sort(reverse=True)\nprint(nums[1])\n"},
            {"turn_number": 3, "candidate_prompt": "If there are fewer than two numbers print -1.",
             "response_kind": "code_edit", "response_message": "Done.", "code_after": "nums = [int(x) for x in input().split(',')]\nnums.sort(reverse=True)\nprint(nums[1] if len(nums) > 1 else -1)\n"},
        ],
    },
    "weak": {
        "test_results": _tests(2),
        "conversation": [
            {"turn_number": 1, "candidate_prompt": "Write the complete solution.",
             "response_kind": "refuse", "response_message": "I can't write this for you - tell me what you want built, and I'll write exactly that.", "code_after": None},
            {"turn_number": 2, "candidate_prompt": "ok just print the biggest number",
             "response_kind": "code_edit", "response_message": "Done.", "code_after": "nums = [int(x) for x in input().split(',')]\nprint(max(nums))\n"},
        ],
    },
}

# ---------------------------------------------------------------- Round 2 (AI-assisted automation)
_R2_DESIGN = {"index": 0, "title": "Saving an amount persists a completed record", "preconditions": "Logged in as jordan.rivera@example.com",
              "steps": "1. Log in\n2. Submit amount 250 for owner jordan\n3. Check the record in the database",
              "test_data": "email = jordan.rivera@example.com; password = Passw0rd!2026; amount = 250",
              "expected_result": "Message 'Saved successfully'; the database has a record for jordan with amount 250 and status 'completed'",
              "refinements": []}
_PLANTED = ("rec = API.create_record('jordan', 250)\n"
            "assert rec['ok'] is True  # only the API reporting on itself\n"
            "print('PASS')\n")
_REAL = ("assert UI.login('jordan.rivera@example.com', 'Passw0rd!2026')\n"
         "rec = API.create_record('jordan', 250)\n"
         "assert UI.visible_message() == 'Saved successfully'\n"
         "saved = Database.find(rec['id'])\n"
         "assert saved is not None and saved['amount'] == 250 and saved['status'] == 'completed'\n"
         "teardown(rec['id'])\n"
         "print('PASS')\n")
_PASS_RUN = {"exit_code": 0, "stdout": "PASS\n", "stderr": "", "timed_out": False, "infra_error": False}
_FLAW = "Asserts only the API's own ok flag, never reads the database - persistence is not proven."


def _r2_block(turns, final_code, code_edits=()):
    return [{"tc_index": 0, "label": "TC0 - Saving an amount persists a completed record", "design": _R2_DESIGN,
             "final_code": final_code, "turns": turns, "code_edits": list(code_edits), "execution_result": _PASS_RUN}]


R2_ANSWERS = {
    "strong": _r2_block([
        {"turn_number": 1, "candidate_prompt": "Encode my test: log in with my test data, submit amount 250 for jordan, then check the message and the database record.",
         "response_kind": "code_edit", "response_message": "Encoded.", "code_after": _PLANTED, "planted_flaw": _FLAW},
        {"turn_number": 2, "candidate_prompt": "This only checks the API's ok flag - that's the system reporting on itself. My expected result is about the database. Log in first, assert the message is 'Saved successfully', then use Database.find on the returned id and assert amount 250 and status 'completed'. Tear the record down at the end.",
         "response_kind": "code_edit", "response_message": "Updated as you described.", "code_after": _REAL},
        {"turn_number": 3, "candidate_prompt": "Ran it - PASS. It now fails if the record isn't persisted, because it reads the database rather than the API's own response.",
         "response_kind": "explain", "response_message": "Agreed.", "code_after": None},
    ], _REAL),
    "average": _r2_block([
        {"turn_number": 1, "candidate_prompt": "Automate my test case with my data.",
         "response_kind": "code_edit", "response_message": "Encoded.", "code_after": _PLANTED, "planted_flaw": _FLAW},
        {"turn_number": 2, "candidate_prompt": "Also check the success message is shown.",
         "response_kind": "code_edit", "response_message": "Added.", "code_after": _PLANTED.replace("print('PASS')", "assert UI.visible_message() == 'Saved successfully'\nprint('PASS')")},
    ], _PLANTED.replace("print('PASS')", "assert UI.visible_message() == 'Saved successfully'\nprint('PASS')")),
    "weak": _r2_block([
        {"turn_number": 1, "candidate_prompt": "do it",
         "response_kind": "code_edit", "response_message": "Encoded.", "code_after": _PLANTED, "planted_flaw": _FLAW},
        {"turn_number": 2, "candidate_prompt": "it passed, done",
         "response_kind": "explain", "response_message": "OK.", "code_after": None},
    ], _PLANTED),
}


def r1_submission(tier: str) -> str:
    return json.dumps(R1_ANSWERS[tier], indent=2)
