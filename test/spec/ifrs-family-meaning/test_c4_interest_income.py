"""C4: InterestIncome answers the filer's "Interest income" line, never "Other income" (R4, R6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from ifm_support import (
    CIKS,
    EXT_NAMESPACES,
    FILINGS,
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

from src.retriever.xbrl.family_resolution import family_member

FAMILY = "InterestIncome"
INTEREST = "RevenueFromInterest"
NOT_INTEREST = {"FinanceIncome", "OtherIncome"}
# Hand-copied FSDS values (TWD) of the "Interest Income" line.
INTEREST_LINE = {2023: 60_293_900_000, 2024: 87_213_400_000, 2025: 105_739_100_000}


def _interest_line(fy):
    """The value of the income-statement "Interest Income" line (FSDS pre + num)."""
    tag = presented_tag(fy, "IS", "Interest Income")
    return fsds_value(fy, tag)


@pytest.mark.criterion("C4")
def test_interest_income_is_the_interest_line():
    # oracle: FSDS pre/num — the IS "Interest Income" line (an extension in FY2023-24,
    # RevenueFromInterest in FY2025) equals RevenueFromInterest; the IS "Other income" line
    # is FinanceIncome (FY2023-24) or OtherIncome (FY2025); IFRS definitions in FSDS tag
    assert tag_text(INTEREST, "ifrs/2024").endswith("The amount of income arising from interest.")
    assert "other financing activities" in tag_text("FinanceIncome", "ifrs/2024")
    for fy in YEARS:
        line = _interest_line(fy)
        assert line == pytest.approx(INTEREST_LINE[fy], abs=half_unit(INTEREST))
        assert fsds_value(fy, INTEREST) == pytest.approx(line, abs=half_unit(INTEREST))
        other = presented_tag(fy, "IS", "Other income")
        assert other in NOT_INTEREST and fsds_value(fy, other) != pytest.approx(line)
    for fy in YEARS:  # every year: FY2023-24 resolve "Other income" today
        rows = candidate_rows(fy, FAMILY)
        r = lookup(fy, FAMILY, rows)
        assert answer_tag(r, rows) not in NOT_INTEREST, (fy, r.resolution_notes)
        assert r.status.value == "OK", (fy, r.resolution_notes)
        assert r.value == pytest.approx(_interest_line(fy), abs=half_unit(INTEREST))
        assert r.unit == "TWD"
        assert r.substitution is None


@pytest.mark.criterion("C4")
@given(cik=st.sampled_from(CIKS), ifrs_date=st.sampled_from(IFRS_DATES),
       ext_ns=st.sampled_from(EXT_NAMESPACES), rnd=st.randoms(use_true_random=False))
def test_interest_income_for_any_ifrs_filer(cik, ifrs_date, ext_ns, rnd):
    # oracle: R4 and R6 — the FSDS interest line whatever the CIK, namespaces, order and ids
    for fy in YEARS:
        rows = variant_rows(fy, FAMILY, cik, ifrs_date, ext_ns, rnd)
        r = lookup(fy, FAMILY, rows, cik=cik)
        assert answer_tag(r, rows) not in NOT_INTEREST, (fy, r.resolution_notes)
        assert r.status.value == "OK", (fy, r.resolution_notes)
        assert r.value == pytest.approx(fsds_value(fy, INTEREST), abs=half_unit(INTEREST))
        assert r.substitution is None


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(value=st.floats(min_value=-1e13, max_value=1e13, allow_nan=False),
       fact_id=st.integers(min_value=1, max_value=10**9), cik=st.sampled_from(CIKS))
def test_finance_income_never_displaces_interest(value, fact_id, cik):
    # oracle: metamorphic (R4) — a presented FinanceIncome fact of any value at the current
    # period, where RevenueFromInterest is filed, never changes the answer
    for fy in YEARS:
        rows = candidate_rows(fy, FAMILY, cik)
        base = lookup(fy, FAMILY, rows, cik=cik)
        assert base.status.value == "OK", (fy, base.resolution_notes)
        concept = f"https://xbrl.ifrs.org/taxonomy/{FILINGS[fy].ifrs_date}/ifrs-full:FinanceIncome"
        m = family_member(FAMILY, cik, concept)
        if m is None:
            continue  # not a member: the lookup's SQL never returns it
        template = next(r for r in rows if r["fact_id"] == base.citation.fact_id)
        sub = m["substitution"] or {}
        extra = {**template, "fact_id": fact_id + 10**10, "value": value, "concept": concept,
                 "concept_pref_rank": m["rank"], "concept_role": m["role"],
                 "substitution_code": sub.get("code"), "substitution_note": sub.get("note"),
                 "unit": "TWD", "presented": True, "decimals": -5}
        r = lookup(fy, FAMILY, [*rows, extra], cik=cik)
        assert (r.status, r.value, r.unit) == (base.status, base.value, base.unit)
        assert r.citation.fact_id == base.citation.fact_id
