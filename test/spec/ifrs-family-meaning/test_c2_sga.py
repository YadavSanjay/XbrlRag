"""C2: SG&A is never the marketing line on its own without saying so (R2, R6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import (
    CIKS,
    EXT_NAMESPACES,
    IFRS_DATES,
    YEARS,
    candidate_rows,
    fsds_value,
    half_unit,
    lookup,
    presented_tag,
    presented_tags,
    tag_text,
    variant_rows,
)

from src.retriever.xbrl.family_resolution import family_member

FAMILY = "SellingGeneralAndAdministrative"
MARKETING, GA = "SalesAndMarketingExpense", "GeneralAndAdministrativeExpense"
# Hand-copied FSDS values (TWD): marketing, G&A, by fiscal year.
LINES = {2023: (10_590_700_000, 60_872_800_000), 2024: (13_143_600_000, 83_745_000_000),
         2025: (16_918_100_000, 82_304_300_000)}


def _check(r, fy):
    """R2: not OK; or OK with marketing and a substitution; or OK with G&A + marketing."""
    marketing, ga = fsds_value(fy, MARKETING), fsds_value(fy, GA)
    if r.status.value != "OK":
        return
    if r.value == pytest.approx(marketing, abs=half_unit(MARKETING)):
        assert r.substitution is not None, "marketing alone answered SG&A without saying so"
        return
    # a stated sum: within the two facts' half-units
    assert r.value == pytest.approx(marketing + ga, abs=half_unit(MARKETING) + half_unit(GA))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("fy", YEARS)
def test_sga_is_not_silently_marketing(fy):
    # oracle: FSDS pre — separate "General and administrative" and "Marketing" lines and no
    # SG&A total; the us-gaap SG&A definition covers selling and all G&A (FSDS tag)
    assert presented_tag(fy, "IS", "Marketing") == MARKETING
    assert presented_tag(fy, "IS", "General and administrative") == GA
    assert "SellingGeneralAndAdministrativeExpense" not in presented_tags(fy)
    assert "general and administrative" in tag_text(
        "SellingGeneralAndAdministrativeExpense", "us-gaap/2024").casefold()
    assert (fsds_value(fy, MARKETING), fsds_value(fy, GA)) == pytest.approx(
        LINES[fy], abs=half_unit(GA))  # the fixture holds the values copied above
    for cik in CIKS:
        m = family_member(FAMILY, cik, "ifrs-full:" + MARKETING)
        assert m is None or m["role"] != "primary"
    _check(lookup(fy, FAMILY, candidate_rows(fy, FAMILY)), fy)


@pytest.mark.criterion("C2")
@given(cik=st.sampled_from(CIKS), ifrs_date=st.sampled_from(IFRS_DATES),
       ext_ns=st.sampled_from(EXT_NAMESPACES), rnd=st.randoms(use_true_random=False))
def test_sga_rule_for_any_ifrs_filer(cik, ifrs_date, ext_ns, rnd):
    # oracle: R2 and R6 — the same refusal of the silent marketing answer under any CIK,
    # taxonomy date, extension namespace, row order and fact ids
    m = family_member(FAMILY, cik, f"https://xbrl.ifrs.org/taxonomy/{ifrs_date}/ifrs-full:"
                      + MARKETING)
    assert m is None or m["role"] != "primary"
    for fy in YEARS:
        rows = variant_rows(fy, FAMILY, cik, ifrs_date, ext_ns, rnd)
        _check(lookup(fy, FAMILY, rows, cik=cik), fy)
