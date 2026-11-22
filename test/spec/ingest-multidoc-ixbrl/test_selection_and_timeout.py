"""C8, C9: selection reads the filing index only; the EDGAR read timeout is configured."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from edgar.httpclient import HTTP_MGR
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
    FilingRequest,
    SECFilingDownloader,
    edgar_read_timeout_s,
    filing_date_floor,
    select_filings,
)

ANNUAL = FilingRequest(ticker="ACME", filing_type="10-K", fiscal_year=2025)
SECOND_QUARTER = FilingRequest(ticker="ACME", filing_type="10-Q", fiscal_year=2025, quarter=2)


class IndexFiling:
    """A row of the filing index. Touching anything that fetches filing text is a failure."""

    def __init__(self, form: str, filed: date, report: str | None, accession: str) -> None:
        self.form = form
        self.filing_date = filed
        self.report_date = report
        self.accession_no = accession

    def __repr__(self) -> str:
        return f"IndexFiling({self.form}, {self.filing_date}, {self.report_date})"

    def _fetches_text(self, *_: object) -> None:
        raise AssertionError(f"filing text fetched during selection: {self!r}")

    period_of_report = property(_fetches_text)
    attachments = property(_fetches_text)
    primary_documents = property(_fetches_text)
    sgml = _fetches_text
    full_text_submission = _fetches_text


class FakeCompany:
    """Stands in for edgartools' Company: the index, in memory, filtered as the library does."""

    def __init__(self, filings: list[IndexFiling]) -> None:
        self.filings = filings

    def get_filings(self, *, form: str, filing_date: str | None = None, **_: object) -> list:
        rows = [f for f in self.filings if f.form == form]
        if filing_date:
            low, _sep, high = filing_date.partition(":")
            rows = [f for f in rows if f.filing_date.isoformat() >= low]
            if high:
                rows = [f for f in rows if f.filing_date.isoformat() <= high]
        return rows


ORIGINAL = IndexFiling("10-K", date(2026, 2, 20), "2025-12-31", "0000000001-26-000010")
PART_III = IndexFiling("10-K/A", date(2026, 4, 28), "2025-12-31", "0000000001-26-000020")
LATER_PART_III = IndexFiling("10-K/A", date(2026, 5, 15), "2025-12-31", "0000000001-26-000030")
PRIOR_YEAR = IndexFiling("10-K", date(2025, 2, 21), "2024-12-31", "0000000001-25-000010")
OLD = IndexFiling("10-K", date(2011, 2, 25), "2010-12-31", "0000000001-11-000010")


@pytest.mark.criterion("C8")
def test_selection_uses_the_index_and_keeps_the_amendment_rule():
    # oracle: annual-amendment-handling C1 by hand — the latest original plus the latest amendment
    # after it. IndexFiling raises if selection asks for filing text
    company = FakeCompany([OLD, PRIOR_YEAR, LATER_PART_III, PART_III, ORIGINAL])
    assert select_filings(company, ANNUAL) == [ORIGINAL, LATER_PART_III]


@pytest.mark.criterion("C8")
def test_a_january_year_end_belongs_to_the_year_it_ends_in():
    # oracle: the existing rule — the year of the period end is the fiscal year (NVDA's FY2026
    # ends in January 2026), so the 2024 period filed in 2025 is not FY2025
    january = IndexFiling("10-K", date(2025, 3, 19), "2025-01-26", "0000000001-25-000040")
    december = IndexFiling("10-K", date(2025, 2, 21), "2024-12-29", "0000000001-25-000050")
    assert select_filings(FakeCompany([january, december]), ANNUAL) == [january]


@pytest.mark.criterion("C8")
def test_quarterly_selection_maps_the_period_month():
    # oracle: the existing rule — quarter = (month - 1) // 3 + 1 of the period end
    first = IndexFiling("10-Q", date(2025, 5, 1), "2025-03-31", "0000000001-25-000060")
    second = IndexFiling("10-Q", date(2025, 8, 1), "2025-06-30", "0000000001-25-000070")
    third = IndexFiling("10-Q", date(2025, 11, 1), "2025-09-30", "0000000001-25-000080")
    assert select_filings(FakeCompany([first, second, third]), SECOND_QUARTER) == [second]


@pytest.mark.criterion("C8")
def test_no_match_raises_and_an_empty_report_date_is_not_a_match():
    # oracle: requirement 8 — an index row without a date cannot be placed, and fetching its text
    # to find out is what this change removes
    undated = IndexFiling("10-K", date(2026, 2, 20), "", "0000000001-26-000090")
    missing = IndexFiling("10-K", date(2026, 2, 21), None, "0000000001-26-000091")
    assert select_filings(FakeCompany([undated, missing, ORIGINAL]), ANNUAL) == [ORIGINAL]
    with pytest.raises(LookupError):
        select_filings(FakeCompany([undated, PRIOR_YEAR]), ANNUAL)


@pytest.mark.criterion("C8")
def test_filing_date_floor_is_the_start_of_the_fiscal_year():
    # oracle: a period ending in year Y cannot be reported before January 1 of Y. Changed by
    # tenq-fiscal-labels (docs/specs/tenq-fiscal-labels.md, requirement 5): a 10-Q request
    # names the fiscal year by the calendar year it ends in, whose first quarter can end in
    # the year before, so the 10-Q floor moves to January 1 of Y - 1 and is pinned there
    # (test/spec/tenq-fiscal-labels/test_c5_requests.py); this test keeps the annual floor
    assert filing_date_floor(ANNUAL) == date(2025, 1, 1)


forms = st.sampled_from(["10-K", "10-K/A"])
filed_days = st.integers(min_value=0, max_value=1400).map(
    lambda days: date(2024, 1, 1) + timedelta(days=days)
)
report_years = st.sampled_from(["2024-12-31", "2025-12-31", "2026-12-31", ""])
accessions = st.from_regex(r"000\d{7}-2\d-\d{6}", fullmatch=True)
rows = st.lists(
    st.builds(IndexFiling, forms, filed_days, report_years, accessions),
    max_size=10,
    unique_by=lambda f: f.accession_no,
)


def _rank(filing: IndexFiling) -> tuple[date, str]:
    return filing.filing_date, filing.accession_no


@pytest.mark.criterion("C8")
@pytest.mark.metamorphic
@given(rows)
def test_selection_matches_the_rule_and_no_text_is_read(filings):
    # oracle: the rule restated here, independently of choose_filings: of the rows reporting
    # FY2025 and filed on or after the floor, the latest original and the latest amendment after
    # it, or the latest amendment alone
    floor = date(2025, 1, 1)
    period = [f for f in filings if f.report_date.startswith("2025") and f.filing_date >= floor]
    originals = [f for f in period if not f.form.endswith("/A")]
    amendments = [f for f in period if f.form.endswith("/A")]
    if originals:
        original = max(originals, key=_rank)
        later = [a for a in amendments if a.filing_date > original.filing_date]
        expected = [original] + ([max(later, key=_rank)] if later else [])
    else:
        expected = [max(amendments, key=_rank)] if amendments else []
    if expected:
        assert select_filings(FakeCompany(filings), ANNUAL) == expected
    else:
        with pytest.raises(LookupError):
            select_filings(FakeCompany(filings), ANNUAL)


@pytest.fixture
def restored_timeout(monkeypatch):
    """Puts the library's process-wide timeout back after the test."""
    monkeypatch.setitem(HTTP_MGR.httpx_params, "timeout", HTTP_MGR.httpx_params.get("timeout"))


@pytest.mark.criterion("C9")
def test_read_timeout_default_and_override(monkeypatch):
    # oracle: requirement 9 — 120 s by default, the variable wins
    monkeypatch.delenv("XBRLRAG_EDGAR_READ_TIMEOUT_S", raising=False)
    assert edgar_read_timeout_s() == pytest.approx(120.0)  # exact: a constant
    monkeypatch.setenv("XBRLRAG_EDGAR_READ_TIMEOUT_S", "45.5")
    assert edgar_read_timeout_s() == pytest.approx(45.5)  # exact: a parsed literal


@pytest.mark.criterion("C9")
def test_downloader_sets_the_timeout_on_the_edgar_client(monkeypatch, tmp_path, restored_timeout):
    # oracle: edgartools' own settings — a read timeout left unset is the library's 30 s
    monkeypatch.setenv("XBRLRAG_EDGAR_READ_TIMEOUT_S", "45")
    SECFilingDownloader(output_root=tmp_path, identity="Spec Test spec@example.com")
    timeout = HTTP_MGR.httpx_params.get("timeout")
    assert getattr(timeout, "read", None) == pytest.approx(45.0)  # exact: the value configured
    assert timeout.connect == pytest.approx(10.0)  # edgartools keeps connect short


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(st.sampled_from(["0", "-1", "abc", "nan", "inf"]))
def test_a_mistyped_timeout_raises(value):
    # oracle: house rule — a mistyped setting raises, it does not silently default
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("XBRLRAG_EDGAR_READ_TIMEOUT_S", value)
        with pytest.raises(ValueError):
            edgar_read_timeout_s()


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(st.floats(min_value=1.0, max_value=3600.0, allow_nan=False))
def test_any_positive_timeout_is_read_back(seconds):
    # oracle: metamorphic — what is written is what is read
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("XBRLRAG_EDGAR_READ_TIMEOUT_S", repr(seconds))
        value = edgar_read_timeout_s()
    assert math.isfinite(value)
    assert value == pytest.approx(seconds)  # exact: repr round-trips a float
