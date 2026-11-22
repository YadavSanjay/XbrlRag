"""C3: the rank the rows' values imply (docs/specs/peer-rank-grading.md, requirement 2).

Real values: the dev case int-35111c317c3b-007c39ae (retailers' FY2024 financing cash flow,
SEC companyfacts: HD -0.694bn, AMZN -11.812bn, WMT -13.414bn; AMZN's key rank 2) and the
Walmart accounts-payable case of the conftest (AMZN, WMT, COST, HD; WMT 2nd).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.v2 import value_rank

FIN = [{"ticker": "WMT", "value": -13414000000.0}, {"ticker": "AMZN", "value": -11812000000.0},
       {"ticker": "HD", "value": -694000000.0}]
NAMES = {"WMT": ["Walmart", "Walmart Inc."], "AMZN": ["Amazon"], "COST": ["Costco"],
         "HD": ["Home Depot"]}


@pytest.mark.criterion("C3")
def test_descending_with_negative_values():
    # oracle: value descending — -0.694bn > -11.812bn > -13.414bn: HD 1, AMZN 2, WMT 3
    assert value_rank(FIN, "AMZN", NAMES, 3) == 2
    assert value_rank(FIN, "HD", NAMES, 3) == 1
    assert value_rank(FIN, "WMT", NAMES, 3) == 3


@pytest.mark.criterion("C3")
def test_tie_broken_by_ticker():
    # oracle: ties by ticker ascending — DEVA and DEVB both 100: DEVA 1, DEVB 2; DEVC 3
    rows = [{"ticker": "DEVB", "value": 100.0}, {"ticker": "DEVA", "value": 100.0},
            {"ticker": "DEVC", "value": 50.0}]
    names = {"DEVA": [], "DEVB": [], "DEVC": []}
    assert value_rank(rows, "DEVB", names, 3) == 2
    assert value_rank(rows, "DEVA", names, 3) == 1


@pytest.mark.criterion("C3")
def test_rows_named_by_a_name_map_to_their_ticker():
    # oracle: "Entities" — a row whose entity is "Walmart" is WMT's; by value AMZN 84.981bn,
    # WMT 53.742bn, COST 17.483bn, HD 10.037bn puts WMT 2nd
    rows = [{"entity": "Costco", "value": 17483000000.0},
            {"entity": "Walmart", "value": 53742000000.0},
            {"entity": "Home Depot", "value": 10037000000.0},
            {"entity": "AMZN", "value": 84981000000.0}]
    assert value_rank(rows, "WMT", NAMES, 4) == 2


@pytest.mark.criterion("C3")
def test_first_row_of_an_entity_wins():
    # oracle: "Entities" — DEVA's first valued row (10) counts, not its later 30: DEVB 20 is
    # greater, DEVC 5 is not, so DEVA is 2nd
    rows = [{"ticker": "DEVA", "value": 10.0}, {"ticker": "DEVB", "value": 20.0},
            {"ticker": "DEVA", "value": 30.0}, {"ticker": "DEVC", "value": 5.0}]
    assert value_rank(rows, "DEVA", {"DEVA": [], "DEVB": [], "DEVC": []}, 3) == 2


@pytest.mark.criterion("C3")
def test_no_rank_from_a_partial_list():
    # oracle: "When it can't say" — the complete list gives WMT 3; two rows of a 3-filer
    # ranking, a list without WMT, a WMT row with no value and a single row give None
    assert value_rank(FIN, "WMT", NAMES, 3) == 3
    assert value_rank(FIN[:2], "WMT", NAMES, 3) is None
    assert value_rank(FIN[1:], "WMT", NAMES, 2) is None
    no_value = [{"ticker": "WMT", "value": None}] + FIN[1:]
    assert value_rank(no_value, "WMT", NAMES, 2) is None
    assert value_rank(FIN[:1], "WMT", NAMES, 1) is None


_TICKERS = ["DEVA", "DEVB", "DEVC", "DEVD", "DEVE", "DEVF", "DEVG"]


@st.composite
def _ranking(draw):
    tickers = draw(st.lists(st.sampled_from(_TICKERS), min_size=2, max_size=7, unique=True))
    values = draw(st.lists(st.integers(min_value=-50, max_value=50),
                           min_size=len(tickers), max_size=len(tickers)))
    rows = [{"ticker": t, "value": float(v)} for t, v in zip(tickers, values, strict=True)]
    subject = draw(st.sampled_from(tickers))
    return rows, subject


def _expected(rows, subject):
    # Independent closed form: sort by (value descending, ticker ascending), 1-based index.
    order = sorted(rows, key=lambda r: (-r["value"], r["ticker"]))
    return [r["ticker"] for r in order].index(subject) + 1


@pytest.mark.criterion("C3")
@given(ranking=_ranking(), data=st.data())
def test_any_row_order_gives_the_sorted_position(ranking, data):
    # oracle: requirement 2's closed form, computed in the test by sorting
    rows, subject = ranking
    names = {t: [] for t in _TICKERS}
    shuffled = data.draw(st.permutations(rows))
    got = value_rank(shuffled, subject, names, len(rows))
    assert got == _expected(rows, subject)
    assert 1 <= got <= len(rows)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(ranking=_ranking(), scale=st.sampled_from([0.001, 1.0, 1e6, 1e9]))
def test_positive_scaling_keeps_the_rank(ranking, scale):
    # oracle: requirement 2 — order depends only on the comparison of values, which a
    # positive factor keeps (exact for these integer-valued floats and power-of-ten scales)
    rows, subject = ranking
    names = {t: [] for t in _TICKERS}
    scaled = [{"ticker": r["ticker"], "value": r["value"] * scale} for r in rows]
    assert value_rank(scaled, subject, names, len(rows)) == _expected(rows, subject)
