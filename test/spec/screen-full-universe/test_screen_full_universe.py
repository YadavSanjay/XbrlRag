"""Spec tests for screen-full-universe: a screen ranks every filer of its universe, not the
alphabetically first 50 (docs/specs/screen-full-universe.md).

The database is replaced by this directory's fake store (the I/O boundary), which answers
the per-view queries as Postgres does, LIMIT included. Every case holds more than 50 filers
and runs on each of the screen's four views. Expected values are computed in the tests from
the synthetic store: sorting, `math.fsum` and `statistics.median`.
"""

from __future__ import annotations

import math
import statistics

import pytest
from hypothesis import Phase, given, settings
from hypothesis import strategies as st

FY = 2024
# No shrink phase: a failing universe of 51-150 filers shrinks for minutes per test and
# the minimal example adds nothing (any universe over 50 shows the defect). The profile's
# other settings (example counts, seeds) still apply.
NO_SHRINK = settings(phases=[Phase.explicit, Phase.reuse, Phase.generate])
ALL_VIEWS = ["flat", "derived", "geographic", "segment"]

# 51 to 150 distinct values; integers times 1e3 keep every sum exact in float.
VALUES = st.lists(
    st.integers(min_value=-1_000_000, max_value=1_000_000), min_size=51, max_size=150,
    unique=True,
).map(lambda xs: [x * 1e3 for x in xs])


def _tickers(n: int, start: int = 1) -> list[str]:
    return [f"F{i:03d}" for i in range(start, start + n)]


def _store(values: list[float]) -> dict[str, list[tuple[int, float]]]:
    """Ticker F001.. holds values[0], F002 values[1], ..., all for fiscal year FY."""
    return {t: [(FY, v)] for t, v in zip(_tickers(len(values)), values, strict=True)}


def _by_value(store: dict[str, list[tuple[int, float]]], descending: bool = True) -> list[str]:
    """The tickers with an FY row, sorted by that row's value (the oracle's ranking)."""
    current = {t: v for t, rows in store.items() for y, v in rows if y == FY}
    return sorted(current, key=lambda t: current[t], reverse=descending)


def _row_tickers(result) -> list:
    return [r.get("ticker") for r in result.rows]


def _ranked(result) -> list:
    return (result.to_dict().get("disclosure") or {}).get("ranked")


def _exclusions(result) -> list[tuple]:
    disclosure = result.to_dict().get("disclosure") or {}
    return [(e.get("ticker"), e.get("reason"), e.get("latest_fiscal_year"))
            for e in disclosure.get("exclusions") or []]


# 120 filers whose value grows with the ticker: the largest sit past the 50th ticker.
RISING = [float(i) * 1e6 for i in range(1, 121)]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("view", ALL_VIEWS)
def test_every_filer_past_the_fiftieth_is_ranked(run_screen, view):
    # oracle: Requirements 1 and 4 by hand — F001..F120 hold FY2024 values 1..120 (x1e6), so
    # the ranking is F120, F119, ..., F001; F121..F125 have only FY2023 (period_not_filed,
    # latest 2023); F126..F130 are in the roster with no row (metric_not_tagged)
    store = _store(RISING)
    for t in _tickers(5, start=121):
        store[t] = [(FY - 1, 5e9)]
    roster = _tickers(130)
    result = run_screen(store, roster, FY, view=view)
    expected = _tickers(120)[::-1]
    assert result.status.value == "OK"
    assert _row_tickers(result) == expected
    assert [r.get("rank") for r in result.rows] == list(range(1, 121))
    assert _ranked(result) == expected
    assert _exclusions(result) == (
        [(t, "period_not_filed", FY - 1) for t in _tickers(5, start=121)]
        + [(t, "metric_not_tagged", None) for t in _tickers(5, start=126)]
    )
    assert result.to_dict().get("excluded") == [
        {"ticker": t, "latest_fiscal_year": FY - 1} for t in _tickers(5, start=121)]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("view", ALL_VIEWS)
@NO_SHRINK
@given(values=VALUES)
def test_ranking_is_the_full_sort_for_any_universe(run_screen, view, values):
    # oracle: Requirement 1 — the ranking is every filer sorted by value descending,
    # computed in the test; no filer of the universe is left unranked
    store = _store(values)
    result = run_screen(store, sorted(store), FY, view=view)
    expected = _by_value(store)
    assert _row_tickers(result) == expected
    assert _ranked(result) == expected
    assert _exclusions(result) == []


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@pytest.mark.parametrize("view", ALL_VIEWS)
def test_count_sum_median_over_120_filers(run_screen, view):
    # oracle: hand calculation — values 1..120 (x1e6): count 120, sum 120*121/2 = 7,260 (x1e6),
    # median (60 + 61) / 2 = 60.5 (x1e6)
    result = run_screen(_store(RISING), _tickers(120), FY, view=view)
    got = [r.get("value") for r in result.rows]
    assert len(got) == 120
    # rel 1e-12: the values are exact integers times 1e6; only float() conversion may differ.
    assert math.fsum(got) == pytest.approx(7_260e6, rel=1e-12)
    assert statistics.median(got) == pytest.approx(60.5e6, rel=1e-12)


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("view", ALL_VIEWS)
@NO_SHRINK
@given(values=VALUES)
def test_aggregates_match_the_full_universe(run_screen, view, values):
    # oracle: Requirement 2 — count, math.fsum and statistics.median of the store's values,
    # computed in the test
    store = _store(values)
    result = run_screen(store, sorted(store), FY, view=view)
    got = [r.get("value") for r in result.rows]
    assert len(got) == len(values)
    # rel 1e-12 with abs 1e-6: sums of integers times 1e3 are exact; abs covers a zero sum.
    assert math.fsum(got) == pytest.approx(math.fsum(values), rel=1e-12, abs=1e-6)
    assert statistics.median(got) == pytest.approx(statistics.median(values), rel=1e-12,
                                                   abs=1e-6)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.parametrize("view", ALL_VIEWS)
def test_top_and_bottom_five_past_the_fiftieth(run_screen, view):
    # oracle: by hand — rising values put the five largest on F120..F116; falling values
    # (F001 largest) put the five smallest on F120..F116, ascending from F120
    rising = run_screen(_store(RISING), _tickers(120), FY, view=view, top_n=5, order="desc")
    assert _row_tickers(rising) == ["F120", "F119", "F118", "F117", "F116"]
    assert [r.get("rank") for r in rising.rows] == [1, 2, 3, 4, 5]
    assert _ranked(rising) == _tickers(120)[::-1]

    falling = run_screen(_store(RISING[::-1]), _tickers(120), FY, view=view, top_n=5,
                         order="asc")
    assert _row_tickers(falling) == ["F120", "F119", "F118", "F117", "F116"]
    assert [r.get("rank") for r in falling.rows] == [1, 2, 3, 4, 5]
    assert _ranked(falling) == _tickers(120)[::-1]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("view", ALL_VIEWS)
@NO_SHRINK
@given(values=VALUES, k=st.integers(min_value=1, max_value=10),
       order=st.sampled_from(["desc", "asc"]))
def test_top_n_is_cut_from_the_full_ranking(run_screen, view, values, k, order):
    # oracle: Requirement 3 — the k largest (desc) or smallest (asc) of the full set, sorted
    # in the test; the disclosure keeps the full ranking
    store = _store(values)
    result = run_screen(store, sorted(store), FY, view=view, top_n=k, order=order)
    full = _by_value(store, descending=(order == "desc"))
    assert _row_tickers(result) == full[:k]
    assert [r.get("rank") for r in result.rows] == list(range(1, k + 1))
    assert _ranked(result) == full


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.parametrize("view", ALL_VIEWS)
def test_reversed_assignment_ranks_the_same_values(run_screen, view):
    # oracle: Requirement 5 — values 1..120 (x1e6) in ticker order and in reverse ticker
    # order both rank as 120, 119, ..., 1 (x1e6)
    expected = sorted(RISING, reverse=True)
    for values in (RISING, RISING[::-1]):
        result = run_screen(_store(values), _tickers(120), FY, view=view)
        assert [r.get("value") for r in result.rows] == expected


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@pytest.mark.parametrize("view", ALL_VIEWS)
@NO_SHRINK
@given(data=st.data())
def test_permuting_filers_leaves_the_ranked_values(run_screen, view, data):
    # oracle: Requirement 5 — a permutation of which filer holds which value changes no
    # ranked value; both equal all values sorted descending, computed in the test
    values = data.draw(VALUES, label="values")
    permuted = data.draw(st.permutations(values), label="permuted")
    first = run_screen(_store(values), _tickers(len(values)), FY, view=view)
    second = run_screen(_store(list(permuted)), _tickers(len(values)), FY, view=view)
    expected = sorted(values, reverse=True)
    assert [r.get("value") for r in first.rows] == expected
    assert [r.get("value") for r in second.rows] == expected


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
@pytest.mark.parametrize("view", ALL_VIEWS)
def test_row_limit_caps_after_ranking_and_reports(run_screen, view):
    # oracle: Requirement 6 by hand — rising values 1..120: a cap of 10 returns F120..F111,
    # reports {limit 10, rows_before_cap 120, returned 10}; with top_n=20 the rows before
    # the cap are 20; no cap, or a cap above the row count, adds no row_cap key
    store, roster = _store(RISING), _tickers(120)
    capped = run_screen(store, roster, FY, view=view, row_limit=10)
    assert _row_tickers(capped) == _tickers(10, start=111)[::-1]
    assert [r.get("rank") for r in capped.rows] == list(range(1, 11))
    assert capped.to_dict().get("row_cap") == {"limit": 10, "rows_before_cap": 120,
                                               "returned": 10}
    assert _ranked(capped) == roster[::-1]

    with_top = run_screen(store, roster, FY, view=view, top_n=20, row_limit=10)
    assert _row_tickers(with_top) == _tickers(10, start=111)[::-1]
    assert with_top.to_dict().get("row_cap") == {"limit": 10, "rows_before_cap": 20,
                                                 "returned": 10}

    for uncapped in (run_screen(store, roster, FY, view=view),
                     run_screen(store, roster, FY, view=view, row_limit=None),
                     run_screen(store, roster, FY, view=view, row_limit=200)):
        assert "row_cap" not in uncapped.to_dict()
        assert _row_tickers(uncapped) == roster[::-1]


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("view", ALL_VIEWS)
@NO_SHRINK
@given(data=st.data())
def test_row_limit_is_an_explicit_post_ranking_cut(run_screen, view, data):
    # oracle: Requirement 6 — the k top-ranked of the full ranking sorted in the test, and
    # a row_cap report naming k and the full row count
    values = data.draw(VALUES, label="values")
    k = data.draw(st.integers(min_value=1, max_value=len(values) - 1), label="k")
    store = _store(values)
    result = run_screen(store, sorted(store), FY, view=view, row_limit=k)
    full = _by_value(store)
    assert _row_tickers(result) == full[:k]
    assert result.to_dict().get("row_cap") == {"limit": k, "rows_before_cap": len(values),
                                               "returned": k}
    assert _ranked(result) == full
