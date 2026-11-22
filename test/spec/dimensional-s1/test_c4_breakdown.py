"""C4: a breakdown is one filing's split, the latest that makes one (dimensional-s1 R5)."""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.numerics.restatement import breakdown_filings

GEO = ["StatementGeographicalAxis"]
SEG = ["StatementBusinessSegmentsAxis"]
NVDA_GEO = ["US", "TW", "ChinaIncludingHongKong", "OtherCountries", "SG"]


@pytest.mark.criterion("C4")
def test_breakdown_filings_point_cases(kit):
    # oracle: FSDS num - each listed 10-K reports the year's undimensioned total and a
    # single-dimension member on the axis at the period; filed dates order them (DEI)
    nvda = kit.flat_view(kit.store_rows("NVDA", family="Revenue"))
    assert breakdown_filings(nvda, 2023, GEO) == [kit.NVDA_FY25, kit.NVDA_FY24, kit.NVDA_OWN]
    assert breakdown_filings(nvda, 2023, GEO, as_originally_reported=True) == [kit.NVDA_OWN]
    intc_op = kit.flat_view(kit.store_rows("INTC", family="OperatingIncome"))
    assert breakdown_filings(intc_op, 2022, SEG) == [kit.INTC_FY24, kit.INTC_FY23,
                                                     kit.INTC_OWN]
    # Intel tags its segment revenue only as cells (with ConsolidatedEntitiesAxis or
    # ConsolidationItemsAxis): no single-dimension member, no breakdown filing
    intc_rev = kit.flat_view(kit.store_rows("INTC", family="Revenue"))
    assert breakdown_filings(intc_rev, 2022, SEG) == []
    assert breakdown_filings(intc_rev, 2022, GEO) == [kit.INTC_FY24, kit.INTC_FY23,
                                                      kit.INTC_OWN]
    # no own column for the year: nothing
    assert breakdown_filings(nvda, 2024, GEO) == []


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.lists(st.booleans(), min_size=2, max_size=2),
       st.lists(st.booleans(), min_size=2, max_size=2),
       st.integers(min_value=0, max_value=2**32 - 1))
def test_breakdown_filings_for_any_later_reports(kit, keep, drop_total, seed):
    # oracle: requirement 5 by hand - a later filing qualifies only with both the total
    # and a member at the period; latest filed first; row order does not matter
    rows = kit.flat_view(kit.store_rows("NVDA", family="Revenue"))
    want = []
    for acc, k, d in zip((kit.NVDA_FY25, kit.NVDA_FY24), keep, drop_total, strict=True):
        if not k:
            rows = [r for r in rows if r["accession_no"] != acc]
        elif d:
            rows = [r for r in rows if not (r["accession_no"] == acc
                                            and r["segment_axis"] is None)]
        else:
            want.append(acc)
    random.Random(seed).shuffle(rows)
    assert breakdown_filings(rows, 2023, GEO) == [*want, kit.NVDA_OWN]


def _members(kit, accession, names, tag="Revenues", axis="Geographical"):
    rows = kit.store_rows()
    return [kit.find(rows, accession, tag, f"{axis}={n};") for n in names]


def _assert_rows(result, members, accession, fy):
    assert result.status.value == "OK", result.resolution_notes
    want = sorted(members, key=lambda m: -m["value"])
    assert [r["value"] for r in result.rows] == pytest.approx(
        [m["value"] for m in want], abs=5e5)  # millions precision (decimals -6)
    assert [r["fact_id"] for r in result.rows] == [str(m["fact_id"]) for m in want]
    assert {r["citation"]["accession_no"] for r in result.rows} == {accession}
    assert {r["fiscal_year"] for r in result.rows} == {fy}


@pytest.mark.criterion("C4")
def test_breakdown_is_the_latest_filings_split(kit):
    # oracle: FSDS num - NVIDIA's FY2025 10-K splits FY2023 revenue 26,974 into United
    # States 8,292, Taiwan 6,986, China 5,785, Other countries 3,623 and Singapore 2,288;
    # the FY2023 10-K as filed: 8,292, 6,986, 5,911 (other), 5,785
    rows = kit.store_rows()
    res = kit.run_breakdown(rows, "NVDA", 2023, "Revenue", "geography")
    _assert_rows(res, _members(kit, kit.NVDA_FY25, NVDA_GEO), kit.NVDA_FY25, 2023)
    orig = kit.run_breakdown(rows, "NVDA", 2023, "Revenue", "geography",
                             as_originally_reported=True)
    _assert_rows(orig, _members(kit, kit.NVDA_OWN, NVDA_GEO[:4]), kit.NVDA_OWN, 2023)


@pytest.mark.criterion("C4")
def test_breakdown_skips_a_later_filing_without_a_split(kit):
    # oracle: FSDS num - Intel's FY2024 10-K reports FY2022 segment operating income only
    # for CCG 8,207, DCAI 4,198 and NEX 1,532 as single facts (13,937 against a 2,334 total,
    # no single reconciling line closes it); the FY2023 10-K's six segments sum exactly:
    # -5,977 + 5,569 + 1,300 - 281 + 690 + 1,033 = 2,334
    rows = kit.store_rows()
    res = kit.run_breakdown(rows, "INTC", 2022, "OperatingIncome", "segment")
    names = ["AllOtherSegments", "ClientComputingGroup", "DatacenterAndAI",
             "IntelFoundryServices", "MobileyeSegment", "NetworkAndEdge"]
    members = _members(kit, kit.INTC_FY23, names, tag="OperatingIncomeLoss",
                       axis="BusinessSegments")
    assert sum(m["value"] for m in members) == pytest.approx(2_334e6, abs=5e5)
    _assert_rows(res, members, kit.INTC_FY23, 2022)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.booleans(), st.integers(min_value=0, max_value=2**32 - 1))
def test_breakdown_follows_the_stored_filings_in_any_order(kit, keep_fy24, seed):
    # oracle: requirement 5 - without the FY2025 10-K the FY2024 10-K answers (it repeats
    # the FY2023 10-K's four members, FSDS num), without both the own filing does; the
    # rows always come from one filing and add up to its total
    accs = [kit.NVDA_OWN] + ([kit.NVDA_FY24] if keep_fy24 else [])
    rows = kit.store_rows("NVDA", accessions=accs) + kit.store_rows("INTC")
    random.Random(seed).shuffle(rows)
    res = kit.run_breakdown(rows, "NVDA", 2023, "Revenue", "geography")
    acc = kit.NVDA_FY24 if keep_fy24 else kit.NVDA_OWN
    _assert_rows(res, _members(kit, acc, NVDA_GEO[:4]), acc, 2023)
    assert sum(r["value"] for r in res.rows) == pytest.approx(26_974e6, abs=5e5)
