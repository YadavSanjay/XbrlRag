"""C5: ``XbrlFactstoreScreen.screen`` ranks a member list (docs/specs/named-company-screen.md,
R6).

The database is faked (conftest). The store values are synthetic, chosen so the corpus leader
(AMZN) is not named and the named leader (GOOGL) is not the first named. Expected rows,
ranks, exclusions and disclosure are Requirement 6 (= screen-universe R2, R3, R5 over the
member list) applied by hand to the store.
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.screen_disclosure import screen_disclosure_text

FY = 2024
END = dt.date(2025, 1, 1)  # stored next-midnight end of a Dec-31 FY2024


def _entry(value):
    return [(FY, value, END)]


STORE = {"AMZN": _entry(85.6e9), "GOOGL": _entry(49.3e9), "META": _entry(43.9e9),
         "AAPL": _entry(31.4e9), "MSFT": _entry(29.5e9), "NVDA": _entry(12.9e9)}
ROSTER = sorted([*STORE, "ORCL", "TSLA"])


def _tickers(rows):
    return [r.get("ticker") for r in rows]


def _disclosure(result):
    return result.to_dict().get("disclosure")


def _exclusion_reasons(disclosure):
    return {e["ticker"]: e["reason"] for e in disclosure.get("exclusions", [])}


@pytest.mark.criterion("C5")
def test_c5_top_one_among_the_named(run_screen):
    # oracle: Requirement 6 by hand — among AAPL 31.4, MSFT 29.5, GOOGL 49.3 the largest is
    # GOOGL (AMZN 85.6 leads the corpus but is not named); ranked GOOGL, AAPL, MSFT
    result = run_screen(STORE, ROSTER, FY, top_n=1, universe=["AAPL", "MSFT", "GOOGL"])
    assert result.status.value == "OK"
    assert _tickers(result.rows) == ["GOOGL"]
    assert [r.get("rank") for r in result.rows] == [1]
    disclosure = _disclosure(result)
    assert disclosure["universe"] == "named"
    assert disclosure["members"] == 3
    assert disclosure["ranked"] == ["GOOGL", "AAPL", "MSFT"]
    assert disclosure["exclusions"] == []
    assert screen_disclosure_text(disclosure).startswith(
        "Universe: the named group (3 companies; 3 ranked for FY2024).")


@pytest.mark.criterion("C5")
def test_c5_unranked_members_are_excluded(run_screen):
    # oracle: Requirement 6 by hand — TSLA is on the roster with no row (metric_not_tagged);
    # IBM is not on the roster (not_in_corpus); the list's case does not matter
    result = run_screen(STORE, ROSTER, FY, universe=["msft", "Tsla", "META", "IBM"])
    assert result.status.value == "OK"
    assert _tickers(result.rows) == ["META", "MSFT"]
    assert [r.get("rank") for r in result.rows] == [1, 2]
    disclosure = _disclosure(result)
    assert disclosure["universe"] == "named"
    assert disclosure["members"] == 4
    assert _exclusion_reasons(disclosure) == {"IBM": "not_in_corpus",
                                              "TSLA": "metric_not_tagged"}


@pytest.mark.criterion("C5")
def test_c5_unreadable_list_is_an_error(run_screen):
    # oracle: Requirement 6 by hand — a list Requirement 1 does not read is ERROR with no rows,
    # never a corpus ranking; a readable one beside it ranks
    for bad in (["AAPL"], ["AAPL", "aapl"], []):
        result = run_screen(STORE, ROSTER, FY, top_n=1, universe=bad)
        assert result.status.value == "ERROR", bad
        assert result.rows == []
    ok = run_screen(STORE, ROSTER, FY, top_n=1, universe=["NVDA", "META"])
    assert _tickers(ok.rows) == ["META"]


POOL = sorted(set(ROSTER) | {"IBM"})


@st.composite
def _stores(draw):
    tickers = draw(st.lists(st.sampled_from(sorted(STORE) + ["ORCL", "TSLA"]), unique=True,
                            max_size=8))
    # Distinct values, so the ranking has no ties.
    values = draw(st.lists(st.integers(1, 10**6), min_size=len(tickers),
                           max_size=len(tickers), unique=True))
    return {t: _entry(float(v)) for t, v in zip(tickers, values, strict=True)}


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(_stores(), st.lists(st.sampled_from(POOL), min_size=2, max_size=6, unique=True),
       st.one_of(st.none(), st.just(1)), st.randoms())
def test_c5_member_list_rules_for_any_store(run_screen, store, members, top_n, rnd):
    # oracle: Requirement 6 computed over the fake store — rows are members only, in value
    # order, ranks 1..n; top_n 1 keeps the largest member; ranked and excluded partition the
    # members; order and case of the list do not change the rows
    result = run_screen(store, ROSTER, FY, top_n=top_n, universe=members)
    with_row = sorted((t for t in members if t in store), key=lambda t: -store[t][0][1])
    expected = with_row if top_n is None else with_row[:top_n]
    assert result.status.value == ("OK" if with_row else "NOT_FOUND")
    assert _tickers(result.rows) == expected
    assert [r.get("rank") for r in result.rows] == list(range(1, len(expected) + 1))
    disclosure = _disclosure(result)
    assert disclosure["universe"] == "named"
    assert disclosure["members"] == len(members)
    excluded = set(_exclusion_reasons(disclosure))
    assert set(disclosure["ranked"]) | excluded == set(members)
    assert not set(disclosure["ranked"]) & excluded
    shuffled = [t.lower() for t in members]
    rnd.shuffle(shuffled)
    again = run_screen(store, ROSTER, FY, top_n=top_n, universe=shuffled)
    assert _tickers(again.rows) == expected
