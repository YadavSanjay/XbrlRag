"""C5: the reporting currency by default, the asked currency on request, per-share too (R5, R6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import (
    CIKS,
    EXT_NAMESPACES,
    IFRS_DATES,
    PER_SHARE_FORMS,
    YEARS,
    candidate_rows,
    fsds_value,
    half_unit,
    lookup,
    presented_tag,
    variant_rows,
)

PER_SHARE = {"EarningsPerShareBasic": "BasicEarningsLossPerShare",
             "EarningsPerShareDiluted": "DilutedEarningsLossPerShare"}
MONETARY = {"NetIncome": "ProfitLossAttributableToOwnersOfParent",
            "StockholdersEquity": "EquityAttributableToOwnersOfParent",
            "ComprehensiveIncome": "ComprehensiveIncomeAttributableToOwnersOfParent",
            "OperatingExpenses": "OperatingExpenseExcludingCostOfSales",
            "InterestIncome": "RevenueFromInterest"}
# Hand-copied FY2024 figures (FSDS 2025q2), checked against the fixture.
FY2024 = {("DilutedEarningsLossPerShare", "TWD"): 44.67,
          ("DilutedEarningsLossPerShare", "USD"): 1.36,
          ("Revenue", "TWD"): 2_894_307_700_000, ("Revenue", "USD"): 88_268_000_000,
          ("ProfitLossAttributableToOwnersOfParent", "USD"): 35_327_200_000}


def _element(fy, family):
    if family == "Revenue":
        return presented_tag(fy, "IS", "NET REVENUE")
    return {**PER_SHARE, **MONETARY}[family]


def _expect(fy, family, unit):
    """(value, tolerance, unit shown) from FSDS for the family's element in ``unit``."""
    tag = _element(fy, family)
    uom = unit or "TWD"  # TWD is the reporting currency: every period of the filing has it
    shown = f"{uom}/shares" if family in PER_SHARE else uom
    return fsds_value(fy, tag, uom), half_unit(tag), shown


FAMILIES = sorted({*PER_SHARE, *MONETARY, "Revenue"})


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("unit", [None, "TWD", "USD"])
@pytest.mark.parametrize("fy", YEARS)
def test_each_family_in_the_default_or_asked_currency(fy, unit):
    # oracle: FSDS num — each element filed in TWD (every period) and USD (the latest
    # period only: a convenience translation); unit-aware-lookup R1-R2
    if fy == 2024:
        for (tag, uom), v in FY2024.items():
            assert fsds_value(fy, tag, uom) == pytest.approx(v, abs=half_unit(tag))
    for family in FAMILIES:
        value, tol, shown = _expect(fy, family, unit)
        r = lookup(fy, family, candidate_rows(fy, family), unit=unit)
        assert r.status.value == "OK", (family, unit, r.resolution_notes)
        assert r.value == pytest.approx(value, abs=tol), family  # as filed, to its decimals
        assert r.unit == shown, family


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("fy", YEARS)
def test_a_currency_not_filed_is_not_found(fy):
    # oracle: unit-aware-lookup R1 — a requested unit the latest period does not carry is
    # NOT_FOUND; the per-share unit is matched by its currency (R5), so USD is found
    for family in ("Revenue", "EarningsPerShareDiluted"):
        assert lookup(fy, family, candidate_rows(fy, family), unit="EUR").status.value == (
            "NOT_FOUND")
    r = lookup(fy, "EarningsPerShareDiluted", candidate_rows(fy, "EarningsPerShareDiluted"),
               unit="USD")
    assert r.status.value == "OK", r.resolution_notes
    value, tol, shown = _expect(fy, "EarningsPerShareDiluted", "USD")
    assert (r.value, r.unit) == (pytest.approx(value, abs=tol), shown)


@pytest.mark.criterion("C5")
@given(form=st.sampled_from(PER_SHARE_FORMS), unit=st.sampled_from([None, "TWD", "USD"]),
       family=st.sampled_from(sorted(PER_SHARE)), cik=st.sampled_from(CIKS),
       ifrs_date=st.sampled_from(IFRS_DATES), ext_ns=st.sampled_from(EXT_NAMESPACES),
       rnd=st.randoms(use_true_random=False))
def test_per_share_unit_spelling_never_matters(form, unit, family, cik, ifrs_date, ext_ns, rnd):
    # oracle: R5 and R6 — `shares*C`, `C*shares` and `C/shares` are one unit (C per share),
    # whatever the CIK, namespaces, order and fact ids
    for fy in YEARS:
        value, tol, shown = _expect(fy, family, unit)
        rows = variant_rows(fy, family, cik, ifrs_date, ext_ns, rnd, per_share=form)
        r = lookup(fy, family, rows, unit=unit, cik=cik)
        assert r.status.value == "OK", (fy, form, unit, r.resolution_notes)
        assert r.value == pytest.approx(value, abs=tol)
        assert r.unit == shown
