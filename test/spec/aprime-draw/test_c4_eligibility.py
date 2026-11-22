"""C4: ``fiscal_year_of`` and ``check_eligibility``.

Oracle: requirements 7-8 of docs/specs/aprime-draw.md. Filing-index records are synthetic
(``kit.tenk``); each expected result is worked out by hand from the rules: only form
"10-K" counts, records without a report_date are ignored, January 1-7 belongs to the
previous year, a year with counted 10-Ks but none inline is not_inline, a year with none
is missing.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.aprime.core import check_eligibility, fiscal_year_of

OK = {"eligible": True, "missing_years": [], "not_inline_years": []}


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("date,year", [
    ("2025-01-03", 2024),   # January 1-7 -> previous year
    ("2025-01-07", 2024),   # the last day of the window
    ("2025-01-08", 2025),   # the first day after it
    ("2025-09-27", 2025),
    ("2024-01-01", 2023),   # the first day of the window
    ("2024-12-31", 2024),
])
def test_fiscal_year_of_examples(date, year):
    # oracle: requirement 7 — the date's year, except January 1-7 is the previous year
    assert fiscal_year_of(date) == year


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("date", ["2025-13-01", "2025-02-30", "not-a-date", "", "2025/01/03"])
def test_fiscal_year_of_malformed_raises(date):
    # oracle: requirement 7 — a malformed YYYY-MM-DD date raises ValueError
    with pytest.raises(ValueError):
        fiscal_year_of(date)


@pytest.mark.criterion("C4")
def test_all_three_years_inline_is_eligible(kit):
    # oracle: requirement 8 — every year has an inline 10-K; FY2025 here ends 2026-01-03,
    # which requirement 7 maps to 2025
    filings = [kit.tenk("2023-12-31"), kit.tenk("2024-12-28"), kit.tenk("2026-01-03")]
    assert check_eligibility(filings) == OK


@pytest.mark.criterion("C4")
def test_year_covered_only_by_amendment_is_missing(kit):
    # oracle: requirement 8 — a 10-K/A does not count, so 2024 has no counted 10-K -> missing
    filings = [kit.tenk("2023-09-30"), kit.tenk("2024-09-30", form="10-K/A"),
               kit.tenk("2025-09-30")]
    assert check_eligibility(filings) == {
        "eligible": False, "missing_years": [2024], "not_inline_years": []}


@pytest.mark.criterion("C4")
def test_year_whose_only_10k_is_not_inline(kit):
    # oracle: requirement 8 — 2023 has a counted 10-K, none inline -> not_inline
    filings = [kit.tenk("2023-06-30", inline=False), kit.tenk("2024-06-30"),
               kit.tenk("2025-06-30")]
    assert check_eligibility(filings) == {
        "eligible": False, "missing_years": [], "not_inline_years": [2023]}


@pytest.mark.criterion("C4")
def test_year_with_one_inline_and_one_non_inline_is_fine(kit):
    # oracle: requirement 8 — not_inline only when none of the year's 10-Ks is inline
    filings = [kit.tenk("2023-06-30"), kit.tenk("2024-06-30", inline=False, acc="x1"),
               kit.tenk("2024-06-30", inline=True, acc="x2"), kit.tenk("2025-06-30")]
    assert check_eligibility(filings) == OK


@pytest.mark.criterion("C4")
def test_records_without_report_date_are_ignored(kit):
    # oracle: requirement 8 — records with a missing or empty report_date are ignored, so
    # 2024 stays missing even though two such 10-Ks were filed
    filings = [kit.tenk("2023-06-30"), kit.tenk(None), kit.tenk(""), kit.tenk("2025-06-30")]
    assert check_eligibility(filings) == {
        "eligible": False, "missing_years": [2024], "not_inline_years": []}


@pytest.mark.criterion("C4")
def test_lists_sorted_and_custom_years(kit):
    # oracle: requirement 8 — both lists ascending; years=(2021, 2022, 2023, 2024): 2021 has
    # nothing (missing), 2022 and 2024 only non-inline (not_inline), 2023 inline
    filings = [kit.tenk("2024-03-31", inline=False), kit.tenk("2023-03-31"),
               kit.tenk("2022-03-31", inline=False), kit.tenk("2020-03-31")]
    assert check_eligibility(filings, years=(2021, 2022, 2023, 2024)) == {
        "eligible": False, "missing_years": [2021], "not_inline_years": [2022, 2024]}


# Base scenarios with their hand-worked results (requirement 8).
_SCENARIOS = [
    ([("2023-06-30", True), ("2024-06-30", True), ("2025-06-30", True)], OK),
    ([("2024-06-30", True), ("2025-06-30", False)],
     {"eligible": False, "missing_years": [2023], "not_inline_years": [2025]}),
    ([], {"eligible": False, "missing_years": [2023, 2024, 2025], "not_inline_years": []}),
]
_OTHER_FORMS = ["10-K/A", "10-KT", "20-F", "10-Q", "8-K", "DEF 14A", "40-F", "20-F/A"]


@pytest.mark.criterion("C4")
@given(
    idx=st.integers(0, len(_SCENARIOS) - 1),
    noise=st.lists(
        st.tuples(st.sampled_from(_OTHER_FORMS), st.integers(2022, 2026), st.integers(1, 12),
                  st.integers(1, 28), st.booleans()),
        max_size=10),
    at=st.integers(0, 10),
)
def test_adding_non_10k_records_never_changes_result(kit, idx, noise, at):
    # oracle: requirement 8 — only form == "10-K" counts, so any non-10-K records inserted
    # anywhere leave each scenario's hand-worked result unchanged
    base, expected = _SCENARIOS[idx]
    filings = [kit.tenk(d, inline=i) for d, i in base]
    extra = [kit.tenk(f"{y:04d}-{m:02d}-{d:02d}", form=f, inline=i) for f, y, m, d, i in noise]
    pos = min(at, len(filings))
    assert check_eligibility(filings) == expected
    assert check_eligibility(filings[:pos] + extra + filings[pos:]) == expected
