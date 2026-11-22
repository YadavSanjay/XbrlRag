"""C8: ``check_answer_perturbation`` — values differ unless the underlying lookups are equal.

Oracle: requirement 11 of docs/specs/keyfree-checks.md (answers equal when same length and
every pair within rel_tol * max(|a|, |b|)).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.perturbation import check_answer_perturbation


@pytest.mark.criterion("C8")
def test_changed_answer_with_different_lookups_is_consistent():
    # oracle: requirement 11 — lookups differ and the answers differ (100 vs 250)
    v = check_answer_perturbation([100.0], [250.0], lookups_equal=False)
    assert v["check"] == "answer_perturbation"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    assert v["original"] == [100.0]
    assert v["perturbed"] == [250.0]
    assert v["lookups_equal"] is False
    # tolerance: the default rel_tol is returned as given
    assert v["tolerance"] == pytest.approx(1e-9, rel=1e-12)


@pytest.mark.criterion("C8")
def test_unchanged_answer_with_different_lookups_is_inconsistent():
    # oracle: requirement 11 — the system ignored the perturbation
    v = check_answer_perturbation([100.0], [100.0], lookups_equal=False)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unchanged"


@pytest.mark.criterion("C8")
def test_equal_lookups_require_equal_answers():
    # oracle: requirement 11 — lookups_equal=True: equal answers consistent, different
    # answers inconsistent "changed"
    v = check_answer_perturbation([100.0, 5.0], [100.0, 5.0], lookups_equal=True)
    assert (v["verdict"], v["reason"]) == ("consistent", "match")
    v = check_answer_perturbation([100.0], [101.0], lookups_equal=True)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "changed")


@pytest.mark.criterion("C8")
def test_rel_tol_decides_equality():
    # oracle: |100 - 100.00005| = 5e-5 <= 1e-6 * 100.00005 -> equal at rel_tol 1e-6, not at
    # 1e-9
    a, b = [100.0], [100.00005]
    v = check_answer_perturbation(a, b, lookups_equal=False, rel_tol=1e-6)
    assert v["reason"] == "unchanged"
    v = check_answer_perturbation(a, b, lookups_equal=False, rel_tol=1e-9)
    assert v["reason"] == "match"


@pytest.mark.criterion("C8")
def test_different_lengths_are_different_answers():
    # oracle: requirement 11 — equal needs the same length
    v = check_answer_perturbation([100.0], [100.0, 3.0], lookups_equal=False)
    assert (v["verdict"], v["reason"]) == ("consistent", "match")
    v = check_answer_perturbation([100.0], [100.0, 3.0], lookups_equal=True)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "changed")


@pytest.mark.criterion("C8")
@pytest.mark.parametrize("a,b", [([], [1.0]), ([1.0], []), ([], [])])
def test_empty_list_is_not_applicable(a, b):
    # oracle: requirement 11 — either list empty -> not_applicable "missing_value"
    for eq in (False, True):
        v = check_answer_perturbation(a, b, lookups_equal=eq)
        assert (v["verdict"], v["reason"]) == ("not_applicable", "missing_value")


_vals = st.lists(st.floats(min_value=-1e12, max_value=1e12, allow_nan=False), min_size=1,
                 max_size=5)


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(a=_vals, b=_vals, eq=st.booleans())
def test_identical_lists_and_symmetry(a, b, eq):
    # oracle: requirement 11 — identical lists are equal answers (inconsistent when the
    # lookups differ); equality is symmetric, so swapping the lists keeps the verdict
    v = check_answer_perturbation(a, list(a), lookups_equal=False)
    assert (v["verdict"], v["reason"]) == ("inconsistent", "unchanged")
    ab = check_answer_perturbation(a, b, lookups_equal=eq)
    ba = check_answer_perturbation(b, a, lookups_equal=eq)
    assert (ab["verdict"], ab["reason"]) == (ba["verdict"], ba["reason"])
