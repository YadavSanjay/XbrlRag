"""C3: DeferredRevenue follows key-definitions requirement 3's precedence (requirement 6)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

CWCL = "us-gaap:ContractWithCustomerLiability"
CWCL_CUR = "us-gaap:ContractWithCustomerLiabilityCurrent"
CWCL_NC = "us-gaap:ContractWithCustomerLiabilityNoncurrent"
DR = "us-gaap:DeferredRevenue"
DR_NC = "us-gaap:DeferredRevenueNoncurrent"


def _check(m, value_m: float, basis: str, fact_ids: list[int], mega: float) -> None:
    assert m["status"] == "defined"
    assert m["family"] == "DeferredRevenue"
    assert m["definition"] == "deferred_revenue_total"
    assert m["concept"] == "s4:deferred_revenue_total"
    assert m["basis"] == basis
    assert m["value"] == pytest.approx(value_m * mega, rel=1e-12)  # whole millions: exact
    assert m["fact_ids"] == fact_ids
    assert [p["fact_id"] for p in m["parts"]] == fact_ids
    # the parts add up to the value (same floats, same order)
    assert sum(p["value"] for p in m["parts"]) == pytest.approx(m["value"], rel=1e-12)


@pytest.mark.criterion("C3")
def test_lines_in_both_sections_are_the_total(s4):
    # oracle: hand addition, Filer T: Deferred revenue 3,400 + Deferred revenue, net of current
    # portion 3,600 = 7,000 (M); its tagged rollforward 3,900 is not read (step 1)
    f = s4.build(s4.filer_t(), s4.filer_t_notes())
    m = s4.system("DeferredRevenue", f)
    _check(m, 7_000, "presented_total", [f.ids[CWCL_CUR], f.ids[CWCL_NC]], s4.M)
    assert [p["section"] for p in m["parts"]] == ["current", "noncurrent"]
    assert f.ids[CWCL] not in m["fact_ids"]
    assert m["readings"] == []


@pytest.mark.criterion("C3")
def test_current_line_with_note_total(s4):
    # oracle: Filer G presents Deferred revenue 6,600 (current only); the tagged
    # ContractWithCustomerLiability 8,600 is not below it, so it is the value (step 2, the
    # us-gaap total element); the presented 6,600 is the current_presented reading
    f = s4.build(s4.filer_g(), s4.filer_g_notes())
    m = s4.system("DeferredRevenue", f)
    _check(m, 8_600, "note_total", [f.ids[CWCL]], s4.M)
    assert m["parts"][0]["section"] == "note"
    assert m["parts"][0]["label"] is None
    assert len(m["readings"]) == 1
    r = m["readings"][0]
    assert r["reading"] == "current_presented"
    assert r["value"] == pytest.approx(6_600 * s4.M, rel=1e-12)  # the line's value
    assert r["concept"] == CWCL_CUR
    assert r["fact_ids"] == [f.ids[CWCL_CUR]]


@pytest.mark.criterion("C3")
def test_current_line_plus_note_noncurrent(s4):
    # oracle: hand addition, Filer O: Deferred revenues 9,300 + tagged non-current 1,200 =
    # 10,500 (M), with no tagged total (step 2, second branch); reading 9,300
    f = s4.build(s4.filer_o(), s4.filer_o_notes())
    m = s4.system("DeferredRevenue", f)
    _check(m, 10_500, "current_plus_note_noncurrent", [f.ids[CWCL_CUR], f.ids[CWCL_NC]], s4.M)
    assert [p["section"] for p in m["parts"]] == ["current", "note"]
    assert [(r["reading"], r["concept"]) for r in m["readings"]] == [
        ("current_presented", CWCL_CUR)]
    assert m["readings"][0]["value"] == pytest.approx(9_300 * s4.M, rel=1e-12)  # the line


@pytest.mark.criterion("C3")
def test_deferred_revenue_total_tag_when_no_contract_liability_total(s4):
    # oracle: step 2 order: no ContractWithCustomerLiability, so the DeferredRevenue total
    # 10,500 (not below 9,300) is the value, ahead of the non-current part
    notes = [*s4.filer_o_notes(), s4.note(DR, 10_500 * s4.M)]
    f = s4.build(s4.filer_o(), notes)
    m = s4.system("DeferredRevenue", f)
    _check(m, 10_500, "note_total", [f.ids[DR]], s4.M)


@pytest.mark.criterion("C3")
def test_total_below_the_current_line_is_ignored(s4):
    # oracle: step 2: Filer H's tagged total 1,500 is below its presented Deferred revenue
    # 2,600 (HD shape: a policy-note figure), so it is ignored; no non-current part: 2,600
    f = s4.build(s4.filer_h(), s4.filer_h_notes())
    m = s4.system("DeferredRevenue", f)
    _check(m, 2_600, "no_noncurrent_reported", [f.ids[CWCL_CUR]], s4.M)
    assert m["readings"] == []


@pytest.mark.criterion("C3")
def test_noncurrent_only_line(s4):
    # oracle: step 3, Filer F: one line below Total current liabilities, 945 (M)
    f = s4.build(s4.filer_f(), [])
    m = s4.system("DeferredRevenue", f)
    _check(m, 945, "presented_total", [f.ids["ext:DeferredRevenuesInitialFranchiseFees"]], s4.M)
    assert m["parts"][0]["section"] == "noncurrent"


@pytest.mark.criterion("C3")
def test_no_line_note_total_else_not_presented(s4):
    # oracle: step 4, Filer M: no line, tagged total 770 (M) is the value; without it (and
    # with only a non-current note) nothing is defined
    f = s4.build(s4.filer_m(), [s4.note(CWCL, 770 * s4.M)])
    m = s4.system("DeferredRevenue", f)
    _check(m, 770, "note_total", [f.ids[CWCL]], s4.M)
    assert m["readings"] == []
    none = s4.system("DeferredRevenue", s4.build(s4.filer_m(), [s4.note(DR_NC, 50 * s4.M)]))
    assert none["status"] == "not_presented"
    assert none["value"] is None
    assert none["fact_ids"] == []


@pytest.mark.criterion("C3")
@given(total=st.none() | st.integers(1, 50_000), noncurrent=st.none() | st.integers(1, 9_000))
def test_two_sections_win_over_any_note(s4, total, noncurrent):
    # oracle: step 1: with lines in both sections the value is 3,400 + 3,600 = 7,000 whatever
    # the notes tag
    notes = []
    if total is not None:
        notes.append(s4.note(CWCL, total * s4.M))
    if noncurrent is not None:
        notes.append(s4.note(DR_NC, noncurrent * s4.M))
    m = s4.system("DeferredRevenue", s4.build(s4.filer_t(), notes))
    assert m["basis"] == "presented_total"
    assert m["value"] == pytest.approx(7_000 * s4.M, rel=1e-12)  # whole millions: exact


@pytest.mark.criterion("C3")
@given(total=st.integers(1, 6_599), noncurrent=st.none() | st.integers(1, 9_000))
def test_a_total_below_the_current_line_is_never_the_value(s4, total, noncurrent):
    # oracle: step 2 by hand: Filer G's current line is 6,600; a total below it is ignored, so
    # the value is 6,600 + the non-current part when tagged, else 6,600
    notes = [s4.note(CWCL, total * s4.M)]
    if noncurrent is not None:
        notes.append(s4.note(CWCL_NC, noncurrent * s4.M))
    m = s4.system("DeferredRevenue", s4.build(s4.filer_g(), notes))
    expected = 6_600 + (noncurrent or 0)
    assert m["value"] == pytest.approx(expected * s4.M, rel=1e-12)  # whole millions: exact
    assert m["basis"] == ("no_noncurrent_reported" if noncurrent is None
                          else "current_plus_note_noncurrent")
