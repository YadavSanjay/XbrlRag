"""C5: the unit policy across the four metamorphic checks.

Oracle: requirement 3 of docs/specs/keyfree-checks.md and S6 of
docs/design/XbrlRag_Eval_Semantics.md: values are never compared across units; a missing
value or unit is not_applicable; two different units are inconsistent ("unit_mismatch")
with no numeric comparison; a None anywhere wins over a mismatch.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.metamorphic import (
    check_breakdown_sum,
    check_growth,
    check_q4,
    check_screen_order,
)


def amt(value, unit="USD"):
    return {"value": value, "unit": unit}


@pytest.mark.criterion("C5")
def test_breakdown_rows_in_another_unit_than_the_total():
    # oracle: requirement 3 — USD rows vs a TWD total: unit_mismatch, no comparison, even
    # though the numbers would match
    v = check_breakdown_sum([amt(60.0), amt(40.0)], amt(100.0, "TWD"))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unit_mismatch"
    assert v["observed"] is None


@pytest.mark.criterion("C5")
def test_growth_lookups_in_two_units():
    # oracle: requirement 3 — USD current vs TWD prior: unit_mismatch
    v = check_growth({"value": 0.12, "unit": "pure"}, amt(112.0), amt(100.0, "TWD"))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unit_mismatch"
    assert v["observed"] is None


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("growth_unit", ["pure", "USD", None])
def test_growth_own_unit_is_ignored(growth_unit):
    # oracle: requirement 5 — the growth's unit is ignored (a ratio); (112-100)/100 = 0.12
    v = check_growth({"value": 0.12, "unit": growth_unit}, amt(112.0), amt(100.0))
    assert v["verdict"] == "consistent"


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("which", [0, 1, 2])
def test_q4_with_one_amount_in_another_unit(which):
    # oracle: requirement 3 — any of the three in TWD: unit_mismatch
    units = ["USD", "USD", "USD"]
    units[which] = "TWD"
    v = check_q4(amt(110.0, units[0]), amt(400.0, units[1]), amt(290.0, units[2]))
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unit_mismatch"


@pytest.mark.criterion("C5")
def test_screen_row_unit_differs_from_its_lookup():
    # oracle: requirement 6 — B's row says USD, B's lookup is TWD: unit_mismatch
    looks = {"A": amt(30.0), "B": amt(20.0, "TWD")}
    v = check_screen_order([{"ticker": "A", "unit": "USD"}, {"ticker": "B", "unit": "USD"}],
                           looks)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unit_mismatch"


@pytest.mark.criterion("C5")
def test_screen_lookups_in_two_units():
    # oracle: requirement 6 — rows agree with their own lookups, but the ranking spans USD
    # and TWD (S6: a screen ranks in one unit): unit_mismatch
    looks = {"A": amt(30.0), "B": amt(20.0, "TWD")}
    v = check_screen_order([{"ticker": "A", "unit": "USD"}, {"ticker": "B", "unit": "TWD"}],
                           looks)
    assert v["verdict"] == "inconsistent"
    assert v["reason"] == "unit_mismatch"


@pytest.mark.criterion("C5")
def test_missing_unit_is_not_applicable():
    # oracle: requirement 3 — a None unit -> not_applicable "missing_unit"
    v = check_breakdown_sum([amt(60.0, None), amt(40.0)], amt(100.0))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_unit"
    v = check_q4(amt(110.0), amt(400.0, None), amt(290.0))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_unit"


@pytest.mark.criterion("C5")
def test_missing_value_wins_over_unit_mismatch():
    # oracle: requirement 3 — a None value -> "missing_value", checked before units, so a
    # TWD total does not turn it into unit_mismatch
    v = check_breakdown_sum([amt(None), amt(40.0)], amt(100.0, "TWD"))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_value"
    v = check_q4(amt(110.0), amt(None), amt(290.0, "TWD"))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_value"
    v = check_growth({"value": 0.1, "unit": "pure"}, amt(None), amt(100.0, "TWD"))
    assert v["verdict"] == "not_applicable"
    assert v["reason"] == "missing_value"


_v = st.integers(min_value=1, max_value=10**9)


@pytest.mark.criterion("C5")
@given(a=_v, b=_v, other=st.sampled_from(["TWD", "EUR", "shares", "pure"]),
       which=st.integers(min_value=0, max_value=2))
def test_a_different_unit_is_never_consistent(a, b, other, which):
    # oracle: requirement 3 — whatever the numbers (including exactly matching ones), one
    # compared amount in a different unit is never "consistent"
    units = ["USD", "USD", "USD"]
    units[which] = other
    bd = check_breakdown_sum([amt(float(a), units[0]), amt(float(b), units[1])],
                             amt(float(a + b), units[2]))
    q4 = check_q4(amt(float(a), units[0]), amt(float(a + b), units[1]),
                  amt(float(b), units[2]))
    gr = check_growth({"value": (a - b) / b, "unit": units[2]}, amt(float(a), units[0]),
                      amt(float(b), units[1]))
    assert bd["verdict"] != "consistent"
    assert q4["verdict"] != "consistent"
    if which != 2:  # the growth's own unit is ignored
        assert gr["verdict"] != "consistent"
    else:
        assert gr["verdict"] == "consistent"
