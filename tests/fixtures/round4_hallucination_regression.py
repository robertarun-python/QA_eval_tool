"""
Golden regression fixture for the Round 4 hallucinated-evaluation bug -
see services.round4_evidence_audit's module docstring. This is the
transcript shape that previously let an LLM evaluator produce findings
like "Welcome label was missing" and "zero follow-up questions" with no
supporting evidence anywhere in the actual session.

TEST_CASES is the exact {title, turns: [{turn_number, candidate_prompt,
model_response}, ...]} payload shape scoring_service.score_round4_submission
builds and hands to llm_service.score_round4_conversation /
round4_evidence_audit.audit_round4_findings - importable as-is by either
a pure unit test of the auditor or a full HTTP-level integration test
that replays these exact candidate_prompt/model_response turns.

Turn numbering (global, 1-indexed, across the whole session in payload
order - see round4_evidence_audit.py's Turn numbering note): turn 1 is
the first booking attempt (HTTP 200), turn 2 is the re-run (HTTP 409),
turn 3 is the invalid-credentials login (HTTP 401).
"""


def _step(description: str, status: str, detail: str | None = None) -> dict:
    step = {"description": description, "status": status}
    if detail is not None:
        step["detail"] = detail
    return step


TEST_CASES = [
    {
        "title": "Booking a slot that is already occupied",
        "turns": [
            {
                "turn_number": 1,
                "candidate_prompt": (
                    "POST /appointments/book - create new appointment booking "
                    "add a test case where appointment booking is tried in a slot "
                    "which is already occupied. if API returns 4xx then pass the test else fail"
                ),
                "model_response": {
                    "response_text": "Running the double-booking scenario for slot 5512.",
                    "steps": [
                        _step("Authenticated as the test patient account", "pass", "Authentication succeeded."),
                        _step("Checked availability for slot 5512", "pass", "Availability showed slot 5512 at 09:00 with is_booked=true."),
                        _step("Sent a booking request for slot 5512", "pass", "Booking request for slot 5512 returned HTTP 200 with success=true and status=confirmed."),
                    ],
                    "observed_result": "Booking request for slot 5512 returned HTTP 200 with success=true and status=confirmed, even though the slot was already marked booked.",
                    "status": "fail",
                },
            },
            {
                "turn_number": 2,
                "candidate_prompt": "the API should have given error. it should not have returned 200 right?",
                "model_response": {
                    "response_text": "Re-running the booking check for slot 5512.",
                    "steps": [
                        _step("Authenticated as the test patient account", "pass", "Authentication succeeded."),
                        _step("Checked availability for slot 5512 again", "pass", "Availability again showed slot 5512 booked."),
                        _step("Re-sent the booking request for slot 5512", "pass", "Booking attempt returned HTTP 409 with SLOT_ALREADY_BOOKED."),
                    ],
                    "observed_result": "Booking attempt returned HTTP 409 with SLOT_ALREADY_BOOKED, correctly rejecting the duplicate booking.",
                    "status": "pass",
                },
            },
        ],
    },
    {
        "title": "Invalid login credentials",
        "turns": [
            {
                "turn_number": 1,
                "candidate_prompt": "try to login with invalid creds. if 401 then pass the test else fail the test",
                "model_response": {
                    "response_text": "Running the invalid-credentials login check.",
                    "steps": [
                        _step("Sent login request with invalid credentials", "pass", "Invalid credentials returned HTTP 401 INVALID_CREDENTIALS."),
                    ],
                    "observed_result": "Invalid credentials returned HTTP 401 INVALID_CREDENTIALS.",
                    "status": "pass",
                },
            },
        ],
    },
]

# Global turn positions - see the module docstring's Turn numbering note.
TURN_1_BOOKING_200 = 1
TURN_2_BOOKING_409 = 2
TURN_3_INVALID_LOGIN_401 = 3

# The two hallucinated findings actually produced against a transcript
# like this one, reconstructed as the LLM scorer's raw `findings` output
# shape (schemas.Round4Finding) so they can be replayed through the
# deterministic auditor / the full scoring pipeline in tests.
HALLUCINATED_WELCOME_LABEL_FINDING = {
    "claim": "Welcome label was missing after login",
    "severity": "medium",
    "evidence": [{"turn": TURN_3_INVALID_LOGIN_401, "quote": "Welcome label was missing"}],
}

HALLUCINATED_ZERO_FOLLOWUP_FINDING = {
    "claim": "Candidate asked zero follow-up questions and accepted the result without investigation",
    "severity": "high",
    "evidence": [{
        "turn": TURN_1_BOOKING_200,
        "quote": "Booking request for slot 5512 returned HTTP 200 with success=true and status=confirmed",
    }],
}

# A genuinely well-evidenced finding, for contrast - this one should
# survive the audit unchanged.
GENUINE_FOLLOWUP_FINDING = {
    "claim": "Booking a slot that is already occupied: candidate questioned the unexpected HTTP 200 and re-verified",
    "severity": "low",
    "evidence": [{"turn": TURN_2_BOOKING_409, "quote": "the API should have given error"}],
}
