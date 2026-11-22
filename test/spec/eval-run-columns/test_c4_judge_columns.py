"""C4: the judge's sub-scores map to four case columns (requirement 4)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.run_columns import judge_columns

NONE4 = {"judge_correctness": None, "judge_completeness": None,
         "judge_citation_support": None, "judge_unsupported_claims": None}
FRACTION = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@pytest.mark.criterion("C4")
def test_full_scores_map_to_the_four_columns():
    # oracle: requirement 4's table, values copied through
    out = judge_columns({"correctness": 0.8, "completeness": 0.5, "citation_support": 1.0,
                         "unsupported_claims": 2})
    assert set(out) == set(NONE4)
    # tolerance: values are copied, not computed; abs=1e-12 is the float-equality idiom
    assert out["judge_correctness"] == pytest.approx(0.8, abs=1e-12)
    assert out["judge_completeness"] == pytest.approx(0.5, abs=1e-12)
    assert out["judge_citation_support"] == pytest.approx(1.0, abs=1e-12)
    assert out["judge_unsupported_claims"] == 2
    assert isinstance(out["judge_unsupported_claims"], int)


@pytest.mark.criterion("C4")
def test_no_judge_and_partial_scores_give_none():
    # oracle: requirement 4 — None or a missing key gives None for that column
    assert judge_columns(None) == NONE4
    assert judge_columns({}) == NONE4
    out = judge_columns({"completeness": 0.25})
    assert out["judge_correctness"] is None
    assert out["judge_citation_support"] is None
    assert out["judge_unsupported_claims"] is None
    # tolerance: copied value; abs=1e-12 is the float-equality idiom
    assert out["judge_completeness"] == pytest.approx(0.25, abs=1e-12)


@pytest.mark.criterion("C4")
def test_integral_float_claim_count_becomes_int():
    # oracle: requirement 4 — 2.0 claims is the int 2
    out = judge_columns({"unsupported_claims": 2.0})
    assert out["judge_unsupported_claims"] == 2
    assert type(out["judge_unsupported_claims"]) is int


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("scores", [
    {"correctness": 1.2},
    {"completeness": -0.1},
    {"citation_support": math.nan},
    {"unsupported_claims": -1},
    {"unsupported_claims": 1.5},
    {"correctnes": 0.5},                 # unknown key (a typo)
    {"faithfulness": 0.5},               # unknown key
])
def test_invalid_scores_raise_value_error(scores):
    # oracle: requirement 4's ValueError conditions
    with pytest.raises(ValueError):
        judge_columns(scores)


@pytest.mark.criterion("C4")
@given(
    corr=st.one_of(st.none(), FRACTION),
    comp=st.one_of(st.none(), FRACTION),
    cit=st.one_of(st.none(), FRACTION),
    claims=st.one_of(st.none(), st.integers(0, 50)),
)
def test_in_range_scores_map_one_to_one(corr, comp, cit, claims):
    # oracle: requirement 4 — each present key maps to its column unchanged, each
    # absent key to None
    given_scores = {"correctness": corr, "completeness": comp, "citation_support": cit,
                    "unsupported_claims": claims}
    scores = {k: v for k, v in given_scores.items() if v is not None}
    out = judge_columns(scores)
    assert set(out) == set(NONE4)
    for key, col in (("correctness", "judge_correctness"),
                     ("completeness", "judge_completeness"),
                     ("citation_support", "judge_citation_support")):
        if given_scores[key] is None:
            assert out[col] is None
        else:
            # tolerance: copied value; abs=1e-12 is the float-equality idiom
            assert out[col] == pytest.approx(given_scores[key], abs=1e-12)
    assert out["judge_unsupported_claims"] == claims
