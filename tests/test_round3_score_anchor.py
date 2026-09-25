"""Round 3 correctness can't exceed the real hidden-test pass rate
(scoring_service._anchor_round3_correctness)."""
from app.services.scoring_service import CORRECTNESS_WEIGHT, _anchor_round3_correctness as anchor

TESTS_1_OF_4 = [{"passed": True}] + [{"passed": False}] * 3


def test_generous_ai_correctness_is_capped_and_the_final_score_follows():
    a = anchor({"correctness_score": 90, "final_score": 80}, TESTS_1_OF_4, [])
    assert a["correctness_score"] == 25
    assert a["final_score"] == round(80 - (90 - 25) * CORRECTNESS_WEIGHT)
    assert "1 of 4 hidden tests passed" in a["note"] and "from 80" in a["note"]


def test_no_change_when_the_ai_is_within_the_pass_rate():
    assert anchor({"correctness_score": 25, "final_score": 60}, TESTS_1_OF_4, []) is None
    assert anchor({"correctness_score": 10, "final_score": 60}, TESTS_1_OF_4, []) is None


def test_not_applied_when_the_ai_dropped_the_candidates_requirement():
    """Those failures aren't the candidate's - left to the scorer's own rule and HR."""
    assert anchor({"correctness_score": 90, "final_score": 80}, TESTS_1_OF_4, [{"dropped_requirement": "distinct"}]) is None


def test_final_score_never_goes_below_zero_and_bad_scores_are_left_alone():
    assert anchor({"correctness_score": 100, "final_score": 20}, [{"passed": False}] * 5, [])["final_score"] == 0
    assert anchor({"correctness_score": None, "final_score": 20}, TESTS_1_OF_4, []) is None
    assert anchor({"correctness_score": 90, "final_score": 80}, [], []) is None
