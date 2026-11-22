"""C5: a defined key (s4:debt_total) is grounded by its total or by lines adding up to it."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import grade_v2_grounding

TXN = 97476
HD = 354950
END = "2025-12-31"
TOTAL = 14048000000


def _debt_case(gb, total=TOTAL, cik=TXN, end=END):
    i = gb.inp(cik, "s4:debt_total", "USD", None, end, total, decimals=None,
               selection="statement_sum")
    return gb.case("c-debt", [i])


def _line(gb, fid, local, value, cik=TXN, end=END):
    return gb.rec(fid, cik, local, "USD", None, end, value)


def _pr(g):
    return (g["precision"], g["recall"])


@pytest.mark.criterion("C5")
def test_a_single_fact_equal_to_the_total_is_right(gb):
    # oracle: requirement 8 — LongTermDebt alone equals the 14,048 million total
    g = grade_v2_grounding(_debt_case(gb), [1], {1: _line(gb, 1, "LongTermDebt", TOTAL)})
    assert (*_pr(g), g["right"]) == (1, 1, (1,))


@pytest.mark.criterion("C5")
def test_lines_that_add_up_are_right(gb):
    # oracle: requirement 8 — 1,000 + 13,048 million = 14,048 million, so both lines count
    facts = {1: _line(gb, 1, "LongTermDebtCurrent", 1000000000),
             2: _line(gb, 2, "LongTermDebtNoncurrent", 13048000000)}
    g = grade_v2_grounding(_debt_case(gb), [1, 2], facts)
    assert (*_pr(g), g["right"], g["wrong"]) == (1, 1, (1, 2), ())


@pytest.mark.criterion("C5")
def test_lines_short_of_the_total_are_wrong(gb):
    # oracle: requirement 8, HD-like — 42,150 million cited against a 44,111 million total
    c = _debt_case(gb, total=44111000000, cik=HD, end="2024-01-28")
    f = _line(gb, 1, "LongTermDebt", 42150000000, cik=HD, end="2024-01-28")
    g = grade_v2_grounding(c, [1], {1: f})
    assert (*_pr(g), g["wrong"]) == (0, 0, (1,))


@pytest.mark.criterion("C5")
def test_an_ordinary_slot_claims_its_fact_first(gb):
    # oracle: requirement 8 — debt to equity: the equity fact matches its own slot and is
    # left out of the debt sum, so 1,000 + 13,048 still equals the total; 3 of 3 right
    debt = gb.inp(TXN, "s4:debt_total", "USD", None, END, TOTAL, decimals=None)
    equity = gb.inp(TXN, "us-gaap:StockholdersEquity", "USD", None, END, 16903000000)
    c = gb.case("c-de", [debt, equity], shape="compute.ratio", kind="compute")
    facts = {1: _line(gb, 1, "StockholdersEquity", 16903000000),
             2: _line(gb, 2, "LongTermDebtCurrent", 1000000000),
             3: _line(gb, 3, "LongTermDebtNoncurrent", 13048000000)}
    g = grade_v2_grounding(c, [1, 2, 3], facts)
    assert (*_pr(g), g["evidenced"]) == (1, 1, (0, 1))


# Cut points in whole millions split the total into 1-4 positive lines of at least 1 million.
_cuts = st.lists(st.integers(1, TOTAL // 10**6 - 1), max_size=3, unique=True).map(sorted)


def _parts(cuts):
    bounds = [0, *cuts, TOTAL // 10**6]
    return [(b - a) * 10**6 for a, b in zip(bounds, bounds[1:], strict=False)]


@pytest.mark.criterion("C5")
@given(cuts=_cuts, drop=st.integers(0, 3))
def test_any_split_adds_up_and_any_drop_does_not(gb, cuts, drop):
    # oracle: requirement 8 — all lines sum to the total (right); without one of at least two
    # lines the rest fall short by at least 1 million and no single line equals the total
    parts = _parts(cuts)
    facts = {n + 1: _line(gb, n + 1, f"DebtLine{n}", v) for n, v in enumerate(parts)}
    ids = list(facts)
    assert _pr(grade_v2_grounding(_debt_case(gb), ids, facts)) == (1, 1)
    if len(parts) >= 2:
        kept = [i for k, i in enumerate(ids) if k != drop % len(ids)]
        assert _pr(grade_v2_grounding(_debt_case(gb), kept, facts)) == (0, 0)
