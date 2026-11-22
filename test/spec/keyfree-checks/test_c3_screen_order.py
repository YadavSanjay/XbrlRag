"""C3: ``check_screen_order`` — a screen's ranking agrees with the individual lookups.

Oracle: requirement 6 of docs/specs/keyfree-checks.md (compared/skipped rows, ties,
inversions as pairs i < j in screen order). Expected inversions are listed by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.metamorphic import check_screen_order


def rows(*tickers, unit="USD"):
    return [{"ticker": t, "unit": unit} for t in tickers]


def lk(**values):
    return {t: {"value": v, "unit": "USD"} for t, v in values.items()}


@pytest.mark.criterion("C3")
def test_desc_ranking_matching_lookups_is_consistent():
    # oracle: A 30 >= B 20 >= C 10, desc -> no inversion
    v = check_screen_order(rows("A", "B", "C"), lk(A=30.0, B=20.0, C=10.0), order="desc")
    assert v["check"] == "screen_order"
    assert v["verdict"] == "consistent"
    assert v["reason"] == "match"
    assert v["order"] == "desc"
    assert v["compared"] == ["A", "B", "C"]
    assert v["values"] == [30.0, 20.0, 10.0]
    assert v["skipped"] == []
    assert v["inversions"] == []


@pytest.mark.criterion("C3")
def test_one_swapped_pair_is_one_inversion():
    # oracle: screen A, C, B with lookups 30, 10, 20: pairs (A,C) 30>=10 ok, (A,B) 30>=20
    # ok, (C,B) 10 < 20 -> the only inversion is [C, B]
    v = check_screen_order(rows("A", "C", "B"), lk(A=30.0, B=20.0, C=10.0), order="desc")
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "misordered"
    assert v["inversions"] == [["C", "B"]]


@pytest.mark.criterion("C3")
def test_full_reversal_lists_every_pair_in_order():
    # oracle: screen C, B, A (10, 20, 30) under desc: all 3 pairs inverted, ordered by i
    # then j: (C,B), (C,A), (B,A)
    v = check_screen_order(rows("C", "B", "A"), lk(A=30.0, B=20.0, C=10.0))
    assert v["verdict"] == "inconsistent"
    assert v["inversions"] == [["C", "B"], ["C", "A"], ["B", "A"]]


@pytest.mark.criterion("C3")
def test_ties_may_appear_in_either_order():
    # oracle: requirement 6 — 20 and 20 tie; tied rows may be in either order
    for order in (("B", "A"), ("A", "B")):
        v = check_screen_order(rows(*order), lk(A=20.0, B=20.0))
        assert v["verdict"] == "consistent"
        assert v["inversions"] == []


@pytest.mark.criterion("C3")
def test_near_tie_within_rel_tol():
    # oracle: |100 - 100.00005| = 5e-5 <= 1e-6 * 100.00005 (1.00005e-4) -> tie, so the
    # "wrong" order is accepted; at rel_tol 1e-9 it is an inversion
    r = rows("A", "B")
    looks = lk(A=100.0, B=100.00005)
    assert check_screen_order(r, looks, rel_tol=1e-6)["verdict"] == "consistent"
    assert check_screen_order(r, looks, rel_tol=1e-9)["verdict"] == "inconsistent"


@pytest.mark.criterion("C3")
def test_asc_ranking():
    # oracle: asc requires lookup_i <= lookup_j: C 10, B 20, A 30 is consistent; A, B, C is
    # fully inverted
    looks = lk(A=30.0, B=20.0, C=10.0)
    assert check_screen_order(rows("C", "B", "A"), looks, order="asc")["verdict"] == "consistent"
    v = check_screen_order(rows("A", "B", "C"), looks, order="asc")
    assert v["verdict"] == "inconsistent"
    assert v["inversions"] == [["A", "B"], ["A", "C"], ["B", "C"]]


@pytest.mark.criterion("C3")
def test_rows_without_lookup_data_are_skipped():
    # oracle: requirement 6 — D has no lookup, E's lookup value is None: both skipped and
    # never fail the check; A, B, C are compared and in order
    looks = lk(A=30.0, B=20.0, C=10.0)
    looks["E"] = {"value": None, "unit": "USD"}
    v = check_screen_order(rows("A", "D", "B", "E", "C"), looks)
    assert v["verdict"] == "consistent"
    assert v["compared"] == ["A", "B", "C"]
    assert v["skipped"] == ["D", "E"]


@pytest.mark.criterion("C3")
def test_one_compared_row_is_not_applicable():
    # oracle: requirement 6 — fewer than 2 compared rows -> not_applicable "too_few_rows"
    v = check_screen_order(rows("A", "D"), lk(A=30.0))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "too_few_rows"
    assert v["skipped"] == ["D"]


@pytest.mark.criterion("C3")
def test_unknown_order_raises():
    # oracle: requirement 6 — order outside {asc, desc} -> ValueError
    with pytest.raises(ValueError):
        check_screen_order(rows("A", "B"), lk(A=1.0, B=2.0), order="up")


_distinct = st.lists(st.integers(min_value=-10**6, max_value=10**6), min_size=2,
                     max_size=10, unique=True)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(values=_distinct)
def test_sorted_rows_and_their_reversal(values):
    # oracle: requirement 6 — rows sorted by distinct lookups descending: desc consistent;
    # reversed: asc consistent, desc inconsistent with every one of the n(n-1)/2 pairs
    # inverted (distinct integers never tie at rel_tol 1e-9)
    tickers = [f"T{i}" for i in range(len(values))]
    looks = {t: {"value": float(x), "unit": "USD"}
             for t, x in zip(tickers, values, strict=True)}
    ranked = [t for t, _ in sorted(zip(tickers, values, strict=True), key=lambda p: -p[1])]
    assert check_screen_order(rows(*ranked), looks, order="desc")["verdict"] == "consistent"
    rev = list(reversed(ranked))
    assert check_screen_order(rows(*rev), looks, order="asc")["verdict"] == "consistent"
    v = check_screen_order(rows(*rev), looks, order="desc")
    assert v["verdict"] == "inconsistent"
    n = len(values)
    assert len(v["inversions"]) == n * (n - 1) // 2
