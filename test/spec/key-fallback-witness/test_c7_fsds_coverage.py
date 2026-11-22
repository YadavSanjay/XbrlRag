"""C7: ``fsds_coverage`` reports the FSDS quarters a build read.

Oracle: requirement 5 of docs/specs/key-fallback-witness.md; quarter arithmetic done by hand
(point) and in the test (property).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.suites.v2_witness import fsds_coverage


@pytest.mark.criterion("C7")
def test_fixture_quarters_and_the_empty_set(kit):
    # oracle: 2023q1, 2024q1, 2025q1: between the first and the last, 2023q2-q4 and
    # 2024q2-q4 are not given (counted by hand)
    assert fsds_coverage(list(kit.QUARTERS)) == {
        "n": 3, "first": "2023q1", "last": "2025q1",
        "missing": ["2023q2", "2023q3", "2023q4", "2024q2", "2024q3", "2024q4"]}
    assert fsds_coverage(["2026q3", "2026q1", "2026q2"]) == {
        "n": 3, "first": "2026q1", "last": "2026q3", "missing": []}
    assert fsds_coverage([]) == {"n": 0, "first": None, "last": None, "missing": []}


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("bad", [["2025q5"], ["2025Q1"], ["25q1"], ["fsds"], ["2025q1", "2025q1"],
                                 ["2025q0"]])
def test_a_bad_or_repeated_name_raises(bad):
    # oracle: requirement 5: names are <yyyy>q<n>, n in 1-4, each at most once
    with pytest.raises(ValueError):
        fsds_coverage(bad)


def _name(i: int) -> str:
    return f"{2000 + i // 4}q{i % 4 + 1}"


@pytest.mark.criterion("C7")
@given(idx=st.sets(st.integers(0, 120), min_size=1, max_size=40), seed=st.randoms())
def test_order_free_and_first_to_last_is_n_plus_missing(idx, seed):
    # oracle: quarters as consecutive integers (year * 4 + quarter - 1): the result does not
    # depend on the order given, and every quarter from first to last is given or missing
    names = [_name(i) for i in idx]
    shuffled = list(names)
    seed.shuffle(shuffled)
    got = fsds_coverage(shuffled)
    assert got == fsds_coverage(sorted(names))
    lo, hi = min(idx), max(idx)
    assert (got["first"], got["last"], got["n"]) == (_name(lo), _name(hi), len(idx))
    assert got["missing"] == [_name(i) for i in range(lo, hi + 1) if i not in idx]
    assert got["n"] + len(got["missing"]) == hi - lo + 1
