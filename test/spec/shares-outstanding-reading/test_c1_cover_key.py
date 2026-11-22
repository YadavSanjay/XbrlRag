"""C1: cover_shares_key reads the cover count of the original annual report labelled the year.

Oracle: requirement 3 of docs/specs/shares-outstanding-reading.md, applied by hand to the
synthetic payload (filer SSS copies Union Pacific's FY2023-25 counts, quoted in the Notes).
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.core import cover_shares_key


def _cover_entries(doc):
    return doc["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]


@pytest.mark.criterion("C1")
def test_fy2025_cover_key_field_for_field(kit):
    # oracle: payload — the FY2025 10-K's cover reads 593,391,460 on 2026-01-30
    key = cover_shares_key(kit.sss_doc(), kit.LABELS, 2025, kit.YEAR_END[2025])
    assert key == {
        "value": kit.COVER[2025], "unit": "shares", "decimals": None,
        "concept": "dei:EntityCommonStockSharesOutstanding", "cik": kit.SSS_CIK,
        "period_start": None, "period_end": "2026-01-30", "fiscal_year": 2025,
        "calendar_year": None, "accession": kit.S25, "filed": "2026-02-06", "form": "10-K",
        "key_source": "sec_companyfacts", "selection": "cover_page", "policies": ["S2", "S6"],
        "status": "single-source", "verified_by": [],
    }


@pytest.mark.criterion("C1")
def test_each_year_reads_its_own_10k(kit):
    # oracle: payload — covers 609,777,914 / 604,286,378 / 593,391,460 in each year's 10-K
    doc = kit.sss_doc()
    for year in (2023, 2024, 2025):
        key = cover_shares_key(doc, kit.LABELS, year, kit.YEAR_END[year])
        assert key is not None
        assert (key["value"], key["period_end"], key["accession"]) == (
            kit.COVER[year], kit.COVER_DATE[year], kit.OWN_10K[year])


@pytest.mark.criterion("C1")
def test_later_amendment_cover_is_ignored(kit):
    # oracle: requirement 3 — only the original annual report (smallest filed, accn) counts;
    # the 10-K/A's 590,000,000 on 2026-04-10 is a different date
    key = cover_shares_key(kit.sss_doc(), kit.LABELS, 2025, kit.YEAR_END[2025])
    assert key is not None and key["value"] == kit.COVER[2025]
    assert key["accession"] == kit.S25


@pytest.mark.criterion("C1")
def test_cover_dates_outside_the_window_are_never_chosen(kit):
    # oracle: requirement 3 — period_end < end <= filed. The FY2025 10-K gets two extra
    # entries: one on the year end itself, one after its filing date. Only 2026-01-30 is kept.
    doc = kit.sss_doc()
    entries = _cover_entries(doc)
    entries.append(kit.entry(None, "2025-12-31", 111 * kit.M, kit.S25))
    entries.append(kit.entry(None, "2026-02-07", 222 * kit.M, kit.S25))
    key = cover_shares_key(doc, kit.LABELS, 2025, kit.YEAR_END[2025])
    assert key is not None and key["value"] == kit.COVER[2025]
    assert key["period_end"] == "2026-01-30"


@pytest.mark.criterion("C1")
def test_two_values_in_the_original_filing_are_ambiguous(kit):
    # oracle: requirement 3 — the original filing's entries carry two values: None. FY2024
    # (untouched) still reads its cover, so a constant stub cannot pass.
    doc = kit.sss_doc()
    _cover_entries(doc).append(kit.entry(None, "2026-01-29", 1 * kit.M, kit.S25))
    assert cover_shares_key(doc, kit.LABELS, 2025, kit.YEAR_END[2025]) is None
    other = cover_shares_key(doc, kit.LABELS, 2024, kit.YEAR_END[2024])
    assert other is not None and other["value"] == kit.COVER[2024]


@pytest.mark.criterion("C1")
def test_missing_concept_or_unit_gives_none(kit):
    # oracle: requirement 3 — absent concept or unit: None, never an error; the full document
    # still gives the cover count
    without_concept = kit.sss_doc()
    del without_concept["facts"]["dei"]
    other_unit = kit.sss_doc()
    node = other_unit["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]
    node["pure"] = node.pop("shares")
    assert cover_shares_key(without_concept, kit.LABELS, 2025, kit.YEAR_END[2025]) is None
    assert cover_shares_key(other_unit, kit.LABELS, 2025, kit.YEAR_END[2025]) is None
    full = cover_shares_key(kit.sss_doc(), kit.LABELS, 2025, kit.YEAR_END[2025])
    assert full is not None and full["value"] == kit.COVER[2025]


@pytest.mark.criterion("C1")
@given(year=st.sampled_from([2023, 2024, 2025]),
       noise=st.lists(st.tuples(st.sampled_from([2023, 2024, 2025]),
                                st.integers(min_value=1, max_value=10**10)),
                      max_size=4))
def test_result_ignores_other_years_and_later_amendments(kit, year, noise):
    # oracle: requirement 3 — entries of other fiscal years' filings, and of a later 10-K/A,
    # never change a year's reading; it stays that year's own cover count
    doc = kit.sss_doc()
    for other_year, val in noise:
        if other_year != year:
            accn = kit.OWN_10K[other_year]
            _cover_entries(doc).append(kit.entry(None, kit.COVER_DATE[other_year], val, accn))
        else:
            _cover_entries(doc).append(kit.entry(None, "2026-04-11", val, kit.S25A))
    key = cover_shares_key(doc, kit.LABELS, year, kit.YEAR_END[year])
    assert key is not None
    assert (key["value"], key["accession"]) == (kit.COVER[year], kit.OWN_10K[year])


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(vals=st.lists(st.integers(min_value=1, max_value=10**10), min_size=1, max_size=3))
def test_value_is_one_of_the_original_filings_candidates(kit, vals):
    # oracle: requirement 3 — replacing the FY2025 10-K's cover entries with new in-window
    # entries: one distinct value is read back as is, two or more give None
    doc = kit.sss_doc()
    entries = [e for e in _cover_entries(doc) if e["accn"] != kit.S25]
    dates = ["2026-01-28", "2026-01-29", "2026-01-30"]
    entries += [kit.entry(None, dates[i], v, kit.S25) for i, v in enumerate(vals)]
    doc["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"] = entries
    key = cover_shares_key(copy.deepcopy(doc), kit.LABELS, 2025, kit.YEAR_END[2025])
    if len(set(vals)) == 1:
        assert key is not None and key["value"] == vals[0] and key["accession"] == kit.S25
    else:
        assert key is None
        other = cover_shares_key(doc, kit.LABELS, 2023, kit.YEAR_END[2023])
        assert other is not None and other["value"] == kit.COVER[2023]
