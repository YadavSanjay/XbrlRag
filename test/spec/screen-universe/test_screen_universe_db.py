"""Spec tests for screen-universe C6: `XbrlFactstoreScreen.screen` ranks only the universe's
members and returns the disclosure (docs/specs/screen-universe.md).

The database is replaced by the fake store of this directory's conftest (the I/O
boundary). Group memberships come from the committed `configs/eval/v2/groups.yaml`, which
the screen reads by default.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

GROUPS_FILE = Path(__file__).resolve().parents[3] / "configs" / "eval" / "v2" / "groups.yaml"
GROUPS: dict[str, list[str]] = yaml.safe_load(GROUPS_FILE.read_text(encoding="utf-8"))["groups"]

# Synthetic FY2023 net income (USD), shaped like the corpus: stored period ends are the
# next-midnight dates, one day after each fiscal year's last day.
NET_INCOME = {
    "AAPL": [(2023, 96_995e6, dt.date(2023, 10, 1))],
    "MSFT": [(2023, 72_361e6, dt.date(2023, 7, 1))],
    "AVGO": [(2023, 14_082e6, dt.date(2023, 10, 30))],
    "QCOM": [(2023, 7_232e6, dt.date(2023, 9, 25))],
    "TXN": [(2023, 6_510e6, dt.date(2024, 1, 1))],
    "NVDA": [(2023, 4_368e6, dt.date(2023, 1, 30))],
    "AMD": [(2023, 854e6, dt.date(2023, 12, 31))],
    "INTC": [(2022, 8_014e6, dt.date(2023, 1, 1))],
}
# MU is in the corpus with no row; TSM is not in the corpus.
ROSTER = sorted([*NET_INCOME, "MU"])

SEMI_RANKED = ["AVGO", "QCOM", "TXN", "NVDA", "AMD"]
SEMI_STATED = {"AVGO": "2023-10-29", "QCOM": "2023-09-24", "TXN": "2023-12-31",
               "NVDA": "2023-01-29", "AMD": "2023-12-30"}


def _disclosure(result):
    return result.to_dict().get("disclosure")


def _excl(disclosure):
    """Exclusions as (ticker, reason, latest_fiscal_year) for comparison."""
    return [(e.get("ticker"), e.get("reason"), e.get("latest_fiscal_year"))
            for e in disclosure.get("exclusions") or []]


# --------------------------------------------------------------------------- C6
@pytest.mark.criterion("C6")
def test_semiconductors_screen_ranks_only_members(run_screen):
    # oracle: Requirements 2-5 applied by hand to the fake store — members AMD AVGO INTC MU
    # NVDA QCOM TSM TXN; AAPL and MSFT are larger but not members; ranked by value
    # 14.1B > 7.2B > 6.5B > 4.4B > 0.9B; INTC has only FY2022, MU no row, TSM no roster entry;
    # stated ends are the stored dates minus one day
    assert sorted(GROUPS["semiconductors"]) == [
        "AMD", "AVGO", "INTC", "MU", "NVDA", "QCOM", "TSM", "TXN"]
    result = run_screen(NET_INCOME, ROSTER, 2023, universe="semiconductors")
    assert result.status.value == "OK"
    assert [r.get("ticker") for r in result.rows] == SEMI_RANKED
    assert [r.get("rank") for r in result.rows] == [1, 2, 3, 4, 5]
    assert [r.get("period_end") for r in result.rows] == [SEMI_STATED[t] for t in SEMI_RANKED]
    # The values pass through unchanged; a relative tolerance of 1e-12 allows float() only.
    assert result.rows[0].get("value") == pytest.approx(14_082e6, rel=1e-12)
    disclosure = _disclosure(result)
    assert isinstance(disclosure, dict)
    assert disclosure.get("universe") == "semiconductors"
    assert disclosure.get("fiscal_year") == 2023
    assert disclosure.get("members") == 8
    assert disclosure.get("ranked") == SEMI_RANKED
    assert _excl(disclosure) == [("INTC", "period_not_filed", 2022),
                                 ("MU", "metric_not_tagged", None),
                                 ("TSM", "not_in_corpus", None)]
    assert disclosure.get("period_ends") == SEMI_STATED
    assert disclosure.get("period_ends_differ") is True  # 2023-01-29 .. 2023-12-31
    assert result.to_dict().get("excluded") == [{"ticker": "INTC", "latest_fiscal_year": 2022}]


@pytest.mark.criterion("C6")
def test_top_n_cuts_rows_not_the_disclosure(run_screen):
    # oracle: Requirement 5 — `ranked` is every ranked member before top_n; filers cut by
    # top_n are ranked, not excluded
    result = run_screen(NET_INCOME, ROSTER, 2023, top_n=3, universe="semiconductors")
    assert [r.get("ticker") for r in result.rows] == ["AVGO", "QCOM", "TXN"]
    assert [r.get("rank") for r in result.rows] == [1, 2, 3]
    disclosure = _disclosure(result)
    assert isinstance(disclosure, dict)
    assert disclosure.get("ranked") == SEMI_RANKED
    assert [t for t, _, _ in _excl(disclosure)] == ["INTC", "MU", "TSM"]
    assert disclosure.get("period_ends") == SEMI_STATED


@pytest.mark.criterion("C6")
def test_absent_null_and_covered_universe_agree(run_screen):
    # oracle: Requirements 1-2 — absent, null and "covered" all mean every roster filer:
    # members AAPL AMD AVGO INTC MSFT MU NVDA QCOM TXN (TSM is not in the roster)
    absent = run_screen(NET_INCOME, ROSTER, 2023)
    null = run_screen(NET_INCOME, ROSTER, 2023, universe=None)
    covered = run_screen(NET_INCOME, ROSTER, 2023, universe="covered")
    assert absent.to_dict() == null.to_dict() == covered.to_dict()
    assert [r.get("ticker") for r in covered.rows] == ["AAPL", "MSFT", *SEMI_RANKED]
    disclosure = _disclosure(covered)
    assert isinstance(disclosure, dict)
    assert disclosure.get("universe") == "covered"
    assert disclosure.get("members") == 9
    assert disclosure.get("ranked") == ["AAPL", "MSFT", *SEMI_RANKED]
    assert _excl(disclosure) == [("INTC", "period_not_filed", 2022),
                                 ("MU", "metric_not_tagged", None)]
    assert disclosure.get("period_ends") == {**SEMI_STATED, "AAPL": "2023-09-30",
                                             "MSFT": "2023-06-30"}
    assert covered.to_dict().get("excluded") == [{"ticker": "INTC", "latest_fiscal_year": 2022}]


@pytest.mark.criterion("C6")
def test_unknown_universe_is_an_error(run_screen):
    # oracle: Requirement 6 — an unknown universe is an error, never a silent full ranking
    result = run_screen(NET_INCOME, ROSTER, 2023, universe="megacaps")
    assert result.status.value == "ERROR"
    assert result.rows == []
    assert "megacaps" in (result.error or "")


@pytest.mark.criterion("C6")
def test_not_found_still_discloses(run_screen):
    # oracle: Requirements 3 and 5 — no member has FY2024: NOT_FOUND, and the disclosure
    # lists all eight members with their reasons
    result = run_screen(NET_INCOME, ROSTER, 2024, universe="semiconductors")
    assert result.status.value == "NOT_FOUND"
    assert result.rows == []
    disclosure = _disclosure(result)
    assert isinstance(disclosure, dict)
    assert disclosure.get("fiscal_year") == 2024
    assert disclosure.get("members") == 8
    assert disclosure.get("ranked") == []
    assert _excl(disclosure) == [
        ("AMD", "period_not_filed", 2023), ("AVGO", "period_not_filed", 2023),
        ("INTC", "period_not_filed", 2022), ("MU", "metric_not_tagged", None),
        ("NVDA", "period_not_filed", 2023), ("QCOM", "period_not_filed", 2023),
        ("TSM", "not_in_corpus", None), ("TXN", "period_not_filed", 2023)]
    assert disclosure.get("period_ends") == {}
    assert disclosure.get("period_ends_differ") is False


POOL = sorted({t for members in GROUPS.values() for t in members} | {"ORCL", "JPM"})
# Each pool filer's fiscal year-end (month, day); the stored end is the next day.
YEAR_END = {t: [(12, 31), (6, 30), (9, 30), (1, 28), (10, 29), (3, 31)][i % 6]
            for i, t in enumerate(POOL)}
ABSENT = "<absent>"
UNIVERSES = [ABSENT, None, "covered", *sorted(GROUPS)]


@st.composite
def stores(draw):
    tickers = draw(st.lists(st.sampled_from(POOL), unique=True, max_size=12))
    store = {}
    for t in tickers:
        years = draw(st.lists(st.integers(2021, 2025), unique=True, min_size=1, max_size=3))
        entries = []
        for y in years:
            month, day = YEAR_END[t]
            # Distinct across filers, so the ranking has no ties.
            value = float(draw(st.integers(1, 10**6)) * 100 + POOL.index(t))
            entries.append((y, value, dt.date(y, month, day) + dt.timedelta(days=1)))
        store[t] = entries
    return store


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(stores(), st.sets(st.sampled_from(POOL), max_size=6), st.sampled_from(UNIVERSES),
       st.integers(2022, 2025), st.one_of(st.none(), st.integers(1, 8)))
def test_screen_universe_rules_for_any_store(run_screen, store, extra, universe, fy, top_n):
    # oracle: Requirements 2-5 written out over the fake store — rows are members only,
    # ranks 1..n, ranked/excluded partition the members, top_n keeps the first top_n of
    # `ranked`, and `excluded` is the universe-free list restricted to members
    roster = sorted(set(store) | extra)
    fields = {} if universe == ABSENT else {"universe": universe}
    result = run_screen(store, roster, fy, top_n=top_n, **fields)
    members = roster if universe in (ABSENT, None, "covered") else sorted(GROUPS[universe])

    latest = {t: max(e for e in entries if e[0] <= fy)
              for t, entries in store.items() if any(e[0] <= fy for e in entries)}
    at_fy = sorted((t for t in members if t in latest and latest[t][0] == fy),
                   key=lambda t: -latest[t][1])
    expected_rows = at_fy if top_n is None else at_fy[:top_n]

    assert result.status.value == ("OK" if at_fy else "NOT_FOUND")
    assert [r.get("ticker") for r in result.rows] == expected_rows
    assert set(expected_rows) <= set(members)
    assert [r.get("rank") for r in result.rows] == list(range(1, len(result.rows) + 1))
    stated = {t: (latest[t][2] - dt.timedelta(days=1)).isoformat() for t in at_fy}
    assert [r.get("period_end") for r in result.rows] == [stated[t] for t in expected_rows]

    disclosure = _disclosure(result)
    assert isinstance(disclosure, dict)
    assert disclosure.get("universe") == ("covered" if universe in (ABSENT, None) else universe)
    assert disclosure.get("members") == len(members)
    assert disclosure.get("ranked") == at_fy
    assert result.rows == [] or [r.get("ticker") for r in result.rows] == (
        disclosure["ranked"][: top_n or len(at_fy)])
    expected_excl = []
    for t in sorted(set(members) - set(at_fy)):
        if t not in roster:
            expected_excl.append((t, "not_in_corpus", None))
        elif t in latest:
            expected_excl.append((t, "period_not_filed", latest[t][0]))
        else:
            expected_excl.append((t, "metric_not_tagged", None))
    assert _excl(disclosure) == expected_excl
    assert sorted(disclosure["ranked"] + [t for t, _, _ in expected_excl]) == sorted(members)
    assert disclosure.get("period_ends") == stated
    ends = [dt.date.fromisoformat(s) for s in stated.values()]
    differ = len(ends) >= 2 and (max(ends) - min(ends)).days > 92
    assert disclosure.get("period_ends_differ") is differ
    assert result.to_dict().get("excluded") == [
        {"ticker": t, "latest_fiscal_year": latest[t][0]}
        for t in sorted(latest) if t in members and latest[t][0] != fy]
