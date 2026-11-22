"""C7: every family but Debt and DeferredRevenue is unaffected (requirement 1)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.defined_measure import defined_precedence

OTHERS = ["Revenue", "NetIncome", "ComprehensiveIncome", "SharesOutstanding", "DebtIssued",
          "DebtRepaid", "LongTermDebt", "AccountsPayable", "debt", "deferredrevenue", ""]


@pytest.mark.criterion("C7")
def test_other_families_get_no_measure(s4):
    # oracle: requirement 1: only Debt and DeferredRevenue have a defined measure; on the same
    # rows that give a defined Debt (Filer H, 53,400 M by hand) every other family gets None,
    # so its lookup keeps candidate_order
    f = s4.build(s4.filer_h(), s4.filer_h_notes())
    debt = s4.system("Debt", f)
    assert debt["status"] == "defined"
    assert debt["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    for family in OTHERS:
        m = s4.system(family, f)
        assert m is None, family
        assert defined_precedence(m) == "candidate_order"


@pytest.mark.criterion("C7")
@given(family=st.text(max_size=30).filter(lambda t: t not in ("Debt", "DeferredRevenue")))
def test_any_other_family_name_gets_none(s4, family):
    # oracle: requirement 1 (the family test is exact, never a substring or case-folded
    # match); Filer G's DeferredRevenue on the same rows is defined (8,600 M by hand)
    f = s4.build(s4.filer_g(), s4.filer_g_notes())
    assert s4.system(family, f) is None
    dr = s4.system("DeferredRevenue", f)
    assert dr["value"] == pytest.approx(8_600 * s4.M, rel=1e-12)  # the tagged total
