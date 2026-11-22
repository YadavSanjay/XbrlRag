"""C5: a 10-Q request names a fiscal quarter, and every fiscal quarter can be requested.

Oracle: companyfacts ``fy``/``fp`` of every 10-Q (the filers name a fiscal year after the
calendar year it ends in, checked in conftest.py), and ORCL 0001193125-26-389274's own text.
The EDGAR company index is the fake, built from the same companyfacts rows.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.downloader.sec_filing_downloader import (
    FilingRequest,
    filing_date_floor,
    select_filings,
)


def _selected(company, request) -> list[str]:
    """The accessions chosen; a LookupError is the function's own "nothing matches"."""
    try:
        return [f.accession_no for f in select_filings(company, request)]
    except LookupError:
        return []


@pytest.mark.criterion("C5")
def test_six_fiscal_quarter_requests(kit):
    # oracle: companyfacts fy/fp of each quarter (Apple's quarter ended 2025-12-27 is fiscal 2026
    # Q1; Microsoft's ended 2025-12-31 is fiscal 2026 Q2; Micron's ended 2025-11-27 and Oracle's
    # ended 2025-11-30 are Q1 and Q2 of fiscal 2026); ORCL's text for fiscal 2027 Q1
    cases = {
        ("AAPL", 2026, 1): ["0000320193-26-000006"],
        ("MSFT", 2026, 2): ["0001193125-26-027207"],
        ("MU", 2026, 1): ["0000723125-25-000046"],
        ("ORCL", 2026, 2): ["0001193125-25-315925"],
        ("ORCL", 2027, 1): ["0001193125-26-389274"],
        ("NVDA", 2027, 1): ["0001045810-26-000052"],
    }
    got = {key: _selected(kit.FakeCompany(kit.index_of(key[0])),
                          FilingRequest(ticker=key[0], filing_type="10-Q", fiscal_year=key[1],
                                        quarter=key[2]))
           for key in cases}
    assert got == cases


@pytest.mark.criterion("C5")
def test_a_calendar_quarter_no_longer_selects_the_wrong_filing(kit):
    # oracle: companyfacts fp — Apple's quarter ended 2026-03-28 is fiscal Q2, not Q1
    company = kit.FakeCompany(kit.index_of("AAPL"))
    assert _selected(company, FilingRequest(ticker="AAPL", filing_type="10-Q", fiscal_year=2026,
                                            quarter=2)) == ["0000320193-26-000013"]
    assert _selected(company, FilingRequest(ticker="AAPL", filing_type="10-Q", fiscal_year=2026,
                                            quarter=3)) == ["0000320193-26-000020"]


@pytest.mark.criterion("C5")
def test_a_10q_floor_is_a_year_earlier():
    # oracle: requirement 5 — a fiscal year ending in January of Y starts its first quarter's
    # 10-Q in Y - 1; an annual request's floor is unchanged
    assert filing_date_floor(FilingRequest(ticker="ACME", filing_type="10-Q", fiscal_year=2027,
                                           quarter=1)) == date(2026, 1, 1)
    assert filing_date_floor(FilingRequest(ticker="ACME", filing_type="10-K",
                                           fiscal_year=2027)) == date(2027, 1, 1)


tenq_rows = st.sampled_from(["AAPL", "MSFT", "MU", "NVDA", "ORCL"])


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(ticker=tenq_rows, data=st.data())
def test_every_10q_is_selected_by_its_own_fiscal_quarter(kit, ticker, data):
    # oracle: companyfacts fy/fp of the 10-Q (2027 for ORCL's -389274, by its text) —
    # metamorphic: in any index order, that request selects exactly that filing
    row = data.draw(st.sampled_from(kit.tenqs(ticker)))
    index = data.draw(st.permutations(kit.index_of(ticker)))
    acc = row["accession_no"]
    request = FilingRequest(ticker=ticker, filing_type="10-Q", fiscal_year=kit.OWN_FY[acc],
                            quarter=kit.OWN_QUARTER[acc])
    assert _selected(kit.FakeCompany(index), request) == [acc]
