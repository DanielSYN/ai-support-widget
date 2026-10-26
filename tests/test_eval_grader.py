from app.llm import Reply
from evals.run_eval import CASES, grade


def test_every_case_is_well_formed():
    for case in CASES:
        assert case["expect"] in {"answer", "decline", "human", "any"}
        assert case["expect"] != "answer" or case["any_of"]


def test_grader():
    answer_case = {"expect": "answer", "any_of": ["30 days"]}
    assert grade(answer_case, Reply("Returns are accepted within 30 days.", True, False)) == []
    assert grade(answer_case, Reply("Sorry, I don't know.", False, False)) == [
        "said it didn't know", "missing any of ['30 days']"]

    injection = {"expect": "decline", "none_of": ["code is"]}
    assert grade(injection, Reply("Your code is SAVE50!", False, False)) == ["contains 'code is'"]
    assert grade({"expect": "human"}, Reply("Let me get someone.", True, True)) == []
