"""C1: the injection text of a ``<<TOP:<id>:<n>>>`` token (docs/specs/ranked-top-injection.md,
Requirement 1)."""

from __future__ import annotations

import copy

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.retriever.xbrl.ranked_top import ranked_top_text


def _rows(*tickers):
    """Rows in the given (rank) order, with falling values and rank keys 1, 2, ..."""
    return [{"rank": i, "ticker": t, "value": float(100 - i), "fact_id": 900 + i}
            for i, t in enumerate(tickers, start=1)]


def _by_hand(rows, n):
    """Requirement 1, written out: rows[:n], tickered ones, label = 1-based list position."""
    if n < 1:
        return None
    top = []
    for pos, row in enumerate(rows[:n], start=1):
        t = row.get("ticker")
        if isinstance(t, str) and t.strip():
            top.append((t.strip(), pos))
    if not top:
        return None
    if n == 1:
        return top[0][0]
    return ", ".join(f"{t} (#{pos})" for t, pos in top)


@pytest.mark.criterion("C1")
def test_three_ranked_rows_carry_their_ranks():
    # oracle: Requirement 1 by hand — "<ticker> (#<position>)" joined by ", "
    assert ranked_top_text(_rows("CCC", "AAA", "BBB"), 3) == "CCC (#1), AAA (#2), BBB (#3)"


@pytest.mark.criterion("C1")
def test_n_two_takes_the_first_two_positions():
    # oracle: Requirement 1 by hand — the top rows are rows[:2]
    assert ranked_top_text(_rows("CCC", "AAA", "BBB"), 2) == "CCC (#1), AAA (#2)"


@pytest.mark.criterion("C1")
def test_fewer_rows_than_n_injects_what_exists_with_labels():
    # oracle: Requirement 1 by hand — fewer than n rows: every row, still labelled for n >= 2
    assert ranked_top_text(_rows("CCC", "AAA", "BBB"), 5) == "CCC (#1), AAA (#2), BBB (#3)"
    assert ranked_top_text(_rows("DDD"), 3) == "DDD (#1)"


@pytest.mark.criterion("C1")
def test_n_one_injects_the_bare_ticker():
    # oracle: Requirement 1 by hand — n == 1 is the single top ticker with no label
    assert ranked_top_text(_rows("CCC", "AAA", "BBB"), 1) == "CCC"


@pytest.mark.criterion("C1")
def test_an_ascending_screen_is_labelled_in_list_order():
    # oracle: Requirement 1 by hand — a smallest-first screen reports rising values; the label is
    # the list position, never a re-sort by value
    rows = [{"rank": 1, "ticker": "DDD", "value": 5.0}, {"rank": 2, "ticker": "EEE", "value": 7.0},
            {"rank": 3, "ticker": "AAA", "value": 9.0}]
    assert ranked_top_text(rows, 3) == "DDD (#1), EEE (#2), AAA (#3)"


@pytest.mark.criterion("C1")
def test_rank_keys_are_not_used_to_re_sort():
    # oracle: Requirement 1 by hand — the row's `rank` key is not read; list order and position rule
    rows = [{"rank": 3, "ticker": "EEE"}, {"rank": 1, "ticker": "AAA"},
            {"rank": 2, "ticker": "BBB"}]
    assert ranked_top_text(rows, 3) == "EEE (#1), AAA (#2), BBB (#3)"


@pytest.mark.criterion("C1")
def test_tickerless_rows_are_skipped_but_keep_their_position():
    # oracle: Requirement 1 by hand — positions 2 (no ticker key), 4 (empty), 5 (blank), 6 (not a
    # str) are skipped; padded " BBB " is stripped
    rows = [{"ticker": "AAA"}, {"value": 1.0}, {"ticker": "CCC"}, {"ticker": ""},
            {"ticker": "   "}, {"ticker": 7}, {"ticker": " BBB "}]
    assert ranked_top_text(rows, 3) == "AAA (#1), CCC (#3)"
    assert ranked_top_text(rows, 7) == "AAA (#1), CCC (#3), BBB (#7)"


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_nothing_to_inject_gives_none_and_one_ticker_gives_text():
    # oracle: Requirement 1 by hand — None for empty rows, n < 1 or tickerless top rows; a tickered
    # row at position 1 makes it text
    assert ranked_top_text([], 3) is None
    assert ranked_top_text(_rows("AAA"), 0) is None
    assert ranked_top_text([{"value": 1.0}, {"ticker": ""}], 2) is None
    assert ranked_top_text([{"value": 1.0}, {"ticker": "AAA"}], 1) is None
    assert ranked_top_text([{"ticker": "AAA"}, {"value": 1.0}], 2) == "AAA (#1)"


@pytest.mark.criterion("C1")
def test_the_rows_are_not_modified():
    # oracle: Requirement intro — the function never modifies its arguments
    rows = _rows("CCC", "AAA", "BBB")
    before = copy.deepcopy(rows)
    assert ranked_top_text(rows, 2) == "CCC (#1), AAA (#2)"
    assert rows == before


_TICKER = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=5)


@st.composite
def _screens(draw):
    tickers = draw(st.lists(_TICKER, min_size=0, max_size=8, unique=True))
    rows = []
    for t in tickers:
        kind = draw(st.sampled_from(["plain", "plain", "plain", "padded", "missing", "blank"]))
        row = {"value": draw(st.floats(-1e12, 1e12)), "rank": draw(st.integers(1, 9))}
        if kind == "plain":
            row["ticker"] = t
        elif kind == "padded":
            row["ticker"] = f" {t}  "
        elif kind == "blank":
            row["ticker"] = " "
        rows.append(row)
    return rows


@pytest.mark.criterion("C1")
@settings(max_examples=200)
@given(rows=_screens(), n=st.integers(0, 8))
def test_any_screen_gives_the_hand_rule(rows, n):
    # oracle: Requirement 1 written out in _by_hand
    assert ranked_top_text(rows, n) == _by_hand(rows, n)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@settings(max_examples=100)
@given(tickers=st.lists(_TICKER, min_size=1, max_size=6, unique=True), n=st.integers(1, 6),
       values=st.lists(st.floats(-1e9, 1e9), min_size=6, max_size=6),
       ranks=st.permutations([1, 2, 3, 4, 5, 6]), extra=st.lists(_TICKER, max_size=3))
def test_values_ranks_and_rows_past_n_never_change_the_text(tickers, n, values, ranks, extra):
    # oracle: Requirement 1 — only the list order and the tickers of rows[:n] matter
    base = [{"ticker": t} for t in tickers]
    expected = _by_hand(base, n)
    assert expected is not None
    keyed = [{"ticker": t, "value": values[i], "rank": ranks[i]} for i, t in enumerate(tickers)]
    assert ranked_top_text(base, n) == expected
    assert ranked_top_text(keyed, n) == expected
    if len(base) >= n:
        assert ranked_top_text(base + [{"ticker": e + "Z"} for e in extra], n) == expected
