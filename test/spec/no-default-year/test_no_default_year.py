"""Spec tests for no-default-year: a text that writes no year yields no year, never fiscal 2024
(docs/specs/no-default-year.md).

Everything is synthetic mention text and a synthetic filer; the database is faked at
``psycopg2.connect``. Expected years are the years each text writes, read by hand: a text that
writes no year inside the bounds names none. All comparisons are exact integers and strings.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.systems.long_context_arms import filing_targets
from src.retriever.extract_year_util import extract_years_in_range
from src.retriever.html10k.scopes import search_scopes
from src.retriever.xbrl.branch_dispatcher import BranchDispatcher
from src.retriever.xbrl.entity_extractor import ResolvedPeriod
from src.retriever.xbrl.intent_router import XBRL_INTENT, IntentRouterResult
from src.retriever.xbrl.period_evidence import partition_yearless_periods

IN_BOUNDS = st.integers(min_value=2018, max_value=2035)
OUT_OF_BOUNDS = st.one_of(st.integers(min_value=1900, max_value=2017),
                          st.integers(min_value=2036, max_value=2099))
ANCHORS = st.one_of(st.none(), IN_BOUNDS)
YEAR_FORMS = ["{y}", "fiscal {y}", "FY{y}"]
# Mentions with no digit that a resolved period can carry (a relative phrase the extractor
# anchored on last_seen_fy), by hand: none writes a year.
WORD_MENTIONS = ["most recent", "latest fiscal year", "the current year", "this year",
                 "Q3 of the last fiscal year", "most recent quarter"]


def _years(text, bounds=None):
    if bounds is None:
        return extract_years_in_range(text)
    return extract_years_in_range(text, year_range=bounds)


def _assert_flagged(periods, mention):
    assert len(periods) == 1, periods
    p = periods[0]
    assert p.mention == mention
    assert p.ambiguous is True
    assert p.fiscal_year is None
    assert isinstance(p.reason, str) and p.reason.strip()


def _anchor(kit, anchor):
    return None if anchor is None else kit.company(anchor)


def _arm_result(kit, mention, fiscal_year):
    return IntentRouterResult(
        intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=[kit.company(2026)],
        periods=[ResolvedPeriod(mention=mention, fiscal_year=fiscal_year)])


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", [
    "Q3", "H1", "1st quarter", "third quarter", "first 9 months", "the 10-K", "top 3 filers",
    "Q3 results", "",
])
def test_c1_a_text_with_no_year_reads_as_no_year(kit, text):
    # oracle: Requirement 1 by hand — none of these writes a year, so none is read
    assert _years(text, kit.bounds) == []
    assert _years(text) == []


@pytest.mark.criterion("C1")
def test_c1_only_written_years_inside_the_bounds(kit):
    # oracle: Requirement 1 by hand — 2015 and 2040 lie outside 2018-2035 (2015 inside the
    # default 2000-2100); "Q3 2023" writes 2023; "FY24" with its FY marker writes 2024
    assert _years("Q3 2023", kit.bounds) == [2023]
    assert _years("Q3 FY24", kit.bounds) == [2024]
    assert _years("fiscal 2015") == [2015]
    assert _years("fiscal 2015", kit.bounds) == []
    assert _years("FY2040", kit.bounds) == []


@pytest.mark.criterion("C1")
@given(data=st.data())
def test_c1_any_yearless_digit_mention_reads_as_no_year(kit, data):
    # oracle: Requirement 1 by hand — the pools write no year form (conftest), so nothing is read
    mention = data.draw(kit.yearless)
    assert _years(mention, kit.bounds) == []
    assert _years(mention) == []


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(data=st.data(), year=IN_BOUNDS, outside=OUT_OF_BOUNDS, form=st.sampled_from(YEAR_FORMS))
def test_c1_adding_a_year_yields_exactly_that_year(kit, data, year, outside, form):
    # oracle: metamorphic, Requirement 1 — no year written reads []; writing one year Y reads
    # exactly [Y]; writing only a year outside the bounds still reads []
    mention = data.draw(kit.yearless)
    assert _years(mention, kit.bounds) == []
    assert _years(f"{mention} {form.format(y=year)}", kit.bounds) == [year]
    assert _years(f"{mention} {form.format(y=outside)}", kit.bounds) == []


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
@pytest.mark.parametrize("mention", [
    "Q3", "H1", "1st quarter", "first 9 months", "Q3 results", "fiscal 2015",
])
def test_c2_the_extractor_flags_a_mention_that_writes_no_year(kit, mention):
    # oracle: Requirement 2 — no year in 2018-2035 is written: one ambiguous period, no year,
    # with an anchor (last_seen_fy 2025) and without a company
    for anchor in (kit.company(2025), None):
        _assert_flagged(kit.resolve([mention], anchor), mention)


@pytest.mark.criterion("C2")
def test_c2_a_written_year_still_resolves(kit):
    # oracle: Requirement 2 by hand — "Q3 2023" writes 2023 and names Q3; a yearless "Q3" next
    # to it is flagged, never 2024
    periods = kit.resolve(["Q3 2023", "Q3"], kit.company(2025))
    assert [(p.fiscal_year, p.fiscal_period, p.ambiguous) for p in periods[:1]] == [
        (2023, "Q3", False)]
    _assert_flagged(periods[1:], "Q3")


@pytest.mark.criterion("C2")
@given(data=st.data(), anchor=ANCHORS)
def test_c2_any_yearless_digit_mention_is_flagged_for_any_anchor(kit, data, anchor):
    # oracle: Requirements 1-2 by hand — no year written, so one ambiguous period whatever the
    # filer's last_seen_fy (or no filer)
    mention = data.draw(kit.yearless)
    _assert_flagged(kit.resolve([mention], _anchor(kit, anchor)), mention)


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
@given(data=st.data(), anchor=ANCHORS, year=IN_BOUNDS)
def test_c2_the_year_comes_from_the_text_never_the_anchor(kit, data, anchor, year):
    # oracle: metamorphic, Requirement 2 — the yearless mention is flagged; the same mention
    # with " fiscal Y" appended resolves to Y, not ambiguous
    mention = data.draw(kit.yearless)
    company = _anchor(kit, anchor)
    _assert_flagged(kit.resolve([mention], company), mention)
    periods = kit.resolve([f"{mention} fiscal {year}"], company)
    assert [(p.fiscal_year, p.ambiguous) for p in periods] == [(year, False)]


# --------------------------------------------------------------------------- C3
def _degradation(kit, mentions, company):
    periods = kit.resolve(mentions, company)
    kept, ignored = partition_yearless_periods(periods)
    result = IntentRouterResult(intent=XBRL_INTENT.NUMERIC_LOOKUP, companies=[company],
                                periods=kept)
    return periods, kept, ignored, BranchDispatcher._unresolved_reasons(result)


@pytest.mark.criterion("C3")
def test_c3_a_yearless_q3_next_to_a_resolved_year_degrades(kit):
    # oracle: Requirements 2-3 with yearless-period-mention's contract — FY2024 resolves, "Q3"
    # is one ambiguous period with year evidence (a digit): kept, so the query degrades
    periods, kept, ignored, reasons = _degradation(kit, ["fiscal 2024", "Q3"], kit.company(2025))
    assert [(p.fiscal_year, p.fiscal_period, p.ambiguous) for p in periods[:1]] == [
        (2024, "FY", False)]
    _assert_flagged(periods[1:], "Q3")
    assert ignored == []
    assert kept == periods
    assert reasons and any("Q3" in r for r in reasons)


@pytest.mark.criterion("C3")
@given(data=st.data(), anchor=IN_BOUNDS, year=IN_BOUNDS)
def test_c3_any_yearless_digit_mention_degrades_next_to_any_year(kit, data, anchor, year):
    # oracle: Requirements 2-3 by hand — the yearless mention is flagged, never ignored, and
    # named by the dispatcher's unresolved reasons
    mention = data.draw(kit.yearless)
    periods, kept, ignored, reasons = _degradation(
        kit, [f"fiscal {year}", mention], kit.company(anchor))
    assert [(p.fiscal_year, p.ambiguous) for p in periods[:1]] == [(year, False)]
    _assert_flagged(periods[1:], mention)
    assert ignored == []
    assert kept == periods
    assert any(mention in r for r in reasons)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.parametrize("mention", ["most recent", "latest fiscal year",
                                     "Q3 of the last fiscal year", "Q3"])
def test_c4_re_reading_a_yearless_mention_adds_no_year(kit, mention):
    # oracle: Requirement 4 by hand — the period's own fiscal_year 2026; its mention writes no
    # year, so nothing is added (2026, so the answer differs from a 2024 default)
    result = _arm_result(kit, mention, 2026)
    assert filing_targets(result) == ([("EXCO", 2026)], None)
    scopes, reason = search_scopes(result, 5)
    assert reason is None
    assert [s["fiscal_years"] for s in scopes] == [[2026]]
    assert [s["ciks"] for s in scopes] == [[kit.cik]]


@pytest.mark.criterion("C4")
@given(data=st.data(), year=IN_BOUNDS)
def test_c4_any_mention_that_writes_no_year_keeps_only_the_period_year(kit, data, year):
    # oracle: Requirement 4 by hand — exactly the period's fiscal_year
    mention = data.draw(st.one_of(st.sampled_from(WORD_MENTIONS), kit.yearless))
    result = _arm_result(kit, mention, year)
    assert filing_targets(result) == ([("EXCO", year)], None)
    assert [s["fiscal_years"] for s in search_scopes(result, 5)[0]] == [[year]]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(data=st.data(), year=st.integers(min_value=2019, max_value=2035))
def test_c4_only_a_written_range_adds_years(kit, data, year):
    # oracle: metamorphic, Requirement 4 — a yearless mention gives {Y}; replacing it by the
    # range "FY{Y-1} through FY{Y}" adds exactly Y-1
    mention = data.draw(st.one_of(st.sampled_from(WORD_MENTIONS), kit.yearless))
    assert filing_targets(_arm_result(kit, mention, year)) == ([("EXCO", year)], None)
    ranged = _arm_result(kit, f"FY{year - 1} through FY{year}", year)
    assert filing_targets(ranged) == ([("EXCO", year - 1), ("EXCO", year)], None)
    assert [s["fiscal_years"] for s in search_scopes(ranged, 5)[0]] == [[year - 1, year]]
