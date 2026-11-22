"""C10: ``perturb_year`` — shift every year mention in a question by the same delta.

Oracle: requirement 13 of docs/specs/keyfree-checks.md: 4-digit 20dd with no adjacent
digit, and FYdd (any case, two digits, no letter/digit before, no digit after) meaning
2000 + dd; every mention shifted by to - from. Expected strings are written by hand.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keyfree.perturbation import perturb_year


@pytest.mark.criterion("C10")
def test_every_year_shifts_by_the_same_delta():
    # oracle: requirement 13 — 2024 -> 2023 is delta -1: 2024 -> 2023 and 2023 -> 2022
    got = perturb_year("Revenue growth in 2024 vs 2023?", 2024, 2023)
    assert got == {"question": "Revenue growth in 2023 vs 2022?",
                   "perturbation": {"kind": "year", "from": 2024, "to": 2023}}


@pytest.mark.criterion("C10")
def test_fy_forms():
    # oracle: requirement 13 — "FY24" is year 2024 (keeps "FY", gets the shifted last two
    # digits); "FY2024" is a 4-digit mention; "fy24" keeps its case
    assert perturb_year("FY24 and FY2024", 2024, 2025)["question"] == "FY25 and FY2025"
    assert perturb_year("fy24 revenue", 2024, 2025)["question"] == "fy25 revenue"
    assert perturb_year("Fy09 to FY10", 2010, 2011)["question"] == "Fy10 to FY11"


@pytest.mark.criterion("C10")
def test_non_mentions_are_left_alone():
    # oracle: requirement 13 — "20245" (a digit after), "FY245" (a third digit) and "XFY24"
    # (a letter before FY) are not mentions; only the plain 2024 shifts
    got = perturb_year("20245 units, FY245, XFY24 in 2024", 2024, 2025)
    assert got["question"] == "20245 units, FY245, XFY24 in 2025"


@pytest.mark.criterion("C10")
def test_from_year_not_mentioned_returns_none():
    # oracle: requirement 13 — from_year must be among the mentions; control: 2023 is
    assert perturb_year("Revenue in 2023", 2024, 2025) is None
    assert perturb_year("Revenue in 20245", 2024, 2025) is None
    assert perturb_year("Revenue in 2023", 2023, 2025)["question"] == "Revenue in 2025"


@pytest.mark.criterion("C10")
def test_shift_out_of_range_returns_none():
    # oracle: requirement 13 — 2099 + 1 = 2100 is outside 2000-2099; 2000 - 1 likewise.
    # Control: a shift that stays inside works
    assert perturb_year("From 2024 to 2099", 2024, 2025) is None
    assert perturb_year("Since FY00 and 2005", 2005, 2004) is None
    assert perturb_year("From 2024 to 2098", 2024, 2025)["question"] == "From 2025 to 2099"


@pytest.mark.criterion("C10")
def test_same_year_raises():
    # oracle: requirement 13 — from == to -> ValueError
    with pytest.raises(ValueError):
        perturb_year("Revenue in 2024", 2024, 2024)


_year = st.integers(min_value=2010, max_value=2089)


@pytest.mark.criterion("C10")
@pytest.mark.metamorphic
@given(years=st.lists(_year, min_size=1, max_size=4),
       fy_short=st.lists(st.booleans(), min_size=4, max_size=4),
       delta=st.sampled_from([-5, -2, -1, 1, 3, 7]))
def test_shift_then_shift_back_restores_the_question(years, fy_short, delta):
    # oracle: requirement 13 — every mention moves by delta and stays in 2003-2096, so a
    # second shift by -delta restores every mention
    parts = [f"FY{y % 100:02d}" if short else str(y) for y, short in
             zip(years, fy_short[:len(years)], strict=True)]
    q = "Compare " + " and ".join(parts) + "?"
    fwd = perturb_year(q, years[0], years[0] + delta)
    assert fwd["perturbation"] == {"kind": "year", "from": years[0], "to": years[0] + delta}
    assert fwd["question"] != q
    back = perturb_year(fwd["question"], years[0] + delta, years[0])
    assert back["question"] == q
