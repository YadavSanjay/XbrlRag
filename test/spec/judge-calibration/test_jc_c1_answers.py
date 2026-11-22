"""C1: calibration answers come from dev text cases only."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.judge import Claim
from src.eval.core.graders.judge_calibration import CalibrationAnswer, calibration_answer
from src.eval.core.run_columns import SPLITS
from src.eval.core.system import SystemAnswer

QUESTION = "What did AAA say about competition?"
REFERENCE = "Competition is intense; pricing pressure is rising."
KEY_POINTS = ["competition is intense", "pricing pressure rising"]
GEN = "deepseek-flash"


def _case(tags=("split:dev",), key_points=KEY_POINTS, expected_text=REFERENCE) -> EvalCase:
    return EvalCase(
        id="dev-text-1",
        question=QUESTION,
        kind="narrative",
        expected_text=expected_text,
        key_points=list(key_points),
        tags=list(tags),
    )


def _answer(error=None, details=None) -> SystemAnswer:
    if details is None:
        details = [
            {"statement": "Competition is intense.", "fact_ids": [], "chunk_ids": ["c1"]},
            {"statement": "Prices are falling.", "fact_ids": [], "chunk_ids": []},
        ]
    raw = {
        "answer_details": details,
        "execution_result": {"steps": []},
        "narrative_chunks": [{"chunk_id": "c1", "text": "We face intense competition."}],
    }
    return SystemAnswer(text="Competition is intense. Prices are falling.", raw=raw, error=error)


@pytest.mark.criterion("C1")
def test_dev_text_case_builds_an_answer():
    # oracle: requirement 2 and judge-runner-wiring requirement 3 (statement i -> claim s{i};
    # a chunk id cites that chunk's text; no citation -> no passages)
    ans = calibration_answer(_case(), _answer(), answer_id="a1", generator_model=GEN)
    assert ans.answer_id == "a1"
    assert ans.case_id == "dev-text-1"
    assert ans.split == "dev"
    assert ans.generator_model == "deepseek-flash"
    assert ans.question == QUESTION
    assert ans.reference_answer == REFERENCE
    assert list(ans.key_points) == KEY_POINTS
    got = [(c.claim_id, c.text, list(c.cited_passages)) for c in ans.claims]
    assert got == [
        ("s0", "Competition is intense.", ["We face intense competition."]),
        ("s1", "Prices are falling.", []),
    ]


@pytest.mark.criterion("C1")
def test_non_dev_and_ungradable_inputs_are_refused():
    # oracle: requirements 1-2 - held-out, validation and untagged cases never enter; a case
    # without key points, an errored or empty answer and an unknown generator are refused
    for tags in (["split:external"], ["split:heldout_filers"], ["split:validation"], []):
        with pytest.raises(ValueError):
            calibration_answer(_case(tags=tags), _answer(), answer_id="a", generator_model=GEN)
    with pytest.raises(ValueError):
        calibration_answer(_case(key_points=[]), _answer(), answer_id="a", generator_model=GEN)
    with pytest.raises(ValueError):
        calibration_answer(_case(), _answer(error="boom"), answer_id="a", generator_model=GEN)
    with pytest.raises(ValueError):
        calibration_answer(_case(), _answer(details=[]), answer_id="a", generator_model=GEN)
    with pytest.raises(ValueError):
        calibration_answer(_case(), _answer(), answer_id="a", generator_model="no-such-model")


@pytest.mark.criterion("C1")
def test_constructor_checks():
    # oracle: requirement 1
    ok = {
        "answer_id": "a1",
        "case_id": "c",
        "split": "dev",
        "generator_model": "deepseek-flash",
        "question": QUESTION,
        "reference_answer": REFERENCE,
        "key_points": ["k"],
        "claims": [Claim("s0", "x", ["p"])],
    }
    built = CalibrationAnswer(**ok)
    assert built.key_points == ("k",)
    assert isinstance(built.claims, tuple)
    bad = [
        {"split": "external"},
        {"answer_id": "  "},
        {"key_points": []},
        {"key_points": ["k", " "]},
        {"claims": []},
        {"claims": [Claim("s0", "x", ["p"]), Claim("s0", "y", [])]},
        {"generator_model": "no-such-model"},
    ]
    for change in bad:
        with pytest.raises(ValueError):
            CalibrationAnswer(**{**ok, **change})


@pytest.mark.criterion("C1")
@given(st.sampled_from([s for s in SPLITS if s != "dev"]))
def test_every_other_split_is_refused(split):
    # oracle: requirement 1 - only "dev" is accepted, whatever the other split is
    with pytest.raises(ValueError):
        calibration_answer(
            _case(tags=[f"split:{split}"]), _answer(), answer_id="a", generator_model=GEN
        )
    # and the same case tagged dev is accepted
    ok = calibration_answer(_case(), _answer(), answer_id="a", generator_model=GEN)
    assert ok.split == "dev"
