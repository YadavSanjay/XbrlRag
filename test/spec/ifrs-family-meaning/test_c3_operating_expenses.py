"""C3: OperatingExpenses answers the presented "Total operating expenses" (R3, R6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import (
    CIKS,
    EXT_NAMESPACES,
    IFRS_DATES,
    YEARS,
    answer_tag,
    candidate_rows,
    fsds_value,
    half_unit,
    lookup,
    presented_tag,
    tag_text,
    variant_rows,
)

from src.retriever.xbrl.family_resolution import family_member, same_measure

FAMILY = "OperatingExpenses"
ELEMENT = "OperatingExpenseExcludingCostOfSales"
# Hand-copied FSDS values (TWD) of "Total operating expenses".
TOTALS = {2023: 253_833_700_000, 2024: 301_070_400_000, 2025: 345_649_600_000}


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("fy", YEARS)
def test_total_operating_expenses_answer(fy):
    # oracle: FSDS pre ("Total operating expenses" is the element) and num; the IFRS
    # element is "operating expense excluding the cost of sales", the us-gaap
    # OperatingExpenses definition excludes what is included in cost of sales (FSDS tag)
    assert presented_tag(fy, "IS", "Total operating expenses") == ELEMENT
    assert "excluding the cost of sales" in tag_text(ELEMENT, "ifrs/2024")
    assert "except for the portion" in tag_text("OperatingExpenses", "us-gaap/2024")
    want = fsds_value(fy, ELEMENT)
    assert want == pytest.approx(TOTALS[fy], abs=half_unit(ELEMENT))
    m = family_member(FAMILY, CIKS[0], "ifrs-full:" + ELEMENT)
    assert m is not None and m["role"] == "primary"
    assert same_measure(FAMILY, CIKS[0], "ifrs-full:" + ELEMENT) is True
    rows = candidate_rows(fy, FAMILY)
    r = lookup(fy, FAMILY, rows)
    assert r.status.value == "OK", r.resolution_notes
    assert answer_tag(r, rows) == ELEMENT
    assert r.value == pytest.approx(want, abs=half_unit(ELEMENT))  # as filed, to its decimals
    assert r.unit == "TWD"
    assert r.substitution is None


@pytest.mark.criterion("C3")
@given(cik=st.sampled_from(CIKS), ifrs_date=st.sampled_from(IFRS_DATES),
       ext_ns=st.sampled_from(EXT_NAMESPACES), rnd=st.randoms(use_true_random=False))
def test_operating_expenses_for_any_ifrs_filer(cik, ifrs_date, ext_ns, rnd):
    # oracle: R3 and R6 — the FSDS total whatever the CIK, namespaces, order and fact ids
    for fy in YEARS:
        rows = variant_rows(fy, FAMILY, cik, ifrs_date, ext_ns, rnd)
        r = lookup(fy, FAMILY, rows, cik=cik)
        assert r.status.value == "OK", (fy, r.resolution_notes)
        assert answer_tag(r, rows) == ELEMENT
        assert r.value == pytest.approx(fsds_value(fy, ELEMENT), abs=half_unit(ELEMENT))
        assert r.substitution is None
