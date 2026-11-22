"""Spec tests for sign-and-nil C2: a screen never ranks a nil.

docs/specs/sign-and-nil.md, requirement R2.4. The database is faked at `psycopg2.connect`
(conftest.py); the screen, its ranking and its disclosure run for real.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_screen import ScreenStatus


def _nil_exclusions(result) -> list[dict]:
    return [e for e in (result.disclosure or {}).get("exclusions", [])
            if e.get("reason") == "nil"]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("family", ["InterestExpense", "TaxExpense"])
def test_vst_nil_segment_fact_is_not_ranked(fx, family, order):
    # oracle: facts 146221 (f-3000) and 146227 (f-3006) are filed xsi:nil="true" in
    # vistra-20251231.htm; R2.4 by hand: no rankable row -> NOT_FOUND, VST disclosed as nil
    rows = fx.VST_SEGMENT_INTEREST if family == "InterestExpense" else fx.VST_SEGMENT_TAX
    fact_id = str(rows[0]["fact_id"])
    result = fx.run_segment_screen(rows, family, order=order)
    assert result.status is ScreenStatus.NOT_FOUND
    assert result.rows == []
    assert _nil_exclusions(result) == [{"ticker": "VST", "reason": "nil", "fact_id": fact_id}]
    assert any("VST" in n and "nil" in n for n in result.resolution_notes)


TICKERS = ["AAPL", "AMD", "AVGO", "CSCO", "INTC", "MSFT", "MU", "NVDA", "QCOM", "TXN", "VST"]


@st.composite
def _screen_rows(draw):
    """Distinct valued rows and at least one nil row, one per ticker, in any order."""
    tickers = draw(st.lists(st.sampled_from(TICKERS), min_size=2, max_size=len(TICKERS),
                            unique=True))
    n_nil = draw(st.integers(min_value=1, max_value=len(tickers) - 1))
    values = draw(st.lists(st.integers(min_value=-10**11, max_value=10**11),
                           min_size=len(tickers) - n_nil, max_size=len(tickers) - n_nil,
                           unique=True))
    out = []
    for i, ticker in enumerate(tickers):
        value = float(values[i]) if i < len(values) else None
        out.append((ticker, f"{i + 1:010d}", value, 500000 + i))
    return draw(st.permutations(out))


def _rows(fx, spec, sign=1):
    return [fx.segment_row(t, cik, None if v is None else sign * v, fid)
            for t, cik, v, fid in spec]


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(_screen_rows(), st.sampled_from(["asc", "desc"]))
def test_nil_rows_are_never_ranked_and_sign_symmetry_holds(fx, spec, order):
    # oracle: R2.4 written out — the valued rows sorted by value in the requested order,
    # ranks 1..n, every nil row disclosed with reason "nil" and its fact id
    result = fx.run_segment_screen(_rows(fx, spec), "Revenue", member="services", order=order)
    valued = [(t, v) for t, _, v, _ in spec if v is not None]
    expected = [t for t, _ in sorted(valued, key=lambda x: x[1], reverse=order == "desc")]
    assert [r["ticker"] for r in result.rows] == expected
    assert [r["rank"] for r in result.rows] == list(range(1, len(expected) + 1))
    assert sorted(_nil_exclusions(result), key=lambda e: e["ticker"]) == sorted(
        ({"ticker": t, "reason": "nil", "fact_id": str(fid)}
         for t, _, v, fid in spec if v is None), key=lambda e: e["ticker"])

    # metamorphic: negating every value and flipping the order ranks the same tickers in
    # the same order (a screen reads each value with its own sign)
    flipped = fx.run_segment_screen(_rows(fx, spec, sign=-1), "Revenue", member="services",
                                    order="asc" if order == "desc" else "desc")
    assert [r["ticker"] for r in flipped.rows] == expected
    # metamorphic: the order rows arrive in does not matter
    reversed_rows = fx.run_segment_screen(_rows(fx, list(reversed(spec))), "Revenue",
                                          member="services", order=order)
    assert [r["ticker"] for r in reversed_rows.rows] == expected
