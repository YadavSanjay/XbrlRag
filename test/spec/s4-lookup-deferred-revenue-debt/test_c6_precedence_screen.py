"""C6: a defined measure answers over candidate_order; screens rank only defined measures."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.defined_measure import defined_precedence, screen_member
from src.retriever.xbrl.family_resolution import candidate_order

PRECEDENCE = {"defined": "defined", "not_presented": "candidate_order",
              "unit_mismatch": "refuse", "conflicting_facts": "refuse"}
SCREEN = {"defined": "ranked", "not_presented": "metric_not_tagged",
          "unit_mismatch": "excluded", "conflicting_facts": "excluded"}


def _measures(s4):
    """One measure of each status, from the fixture filers."""
    h = s4.filer_h()
    for d in h:
        if d["concept"] == "us-gaap:CommercialPaper":
            d["unit"] = "EUR"
    no_debt = [s4.line("Accounts payable", "us-gaap:AccountsPayableCurrent", 900 * s4.M),
               s4.line(s4.TCL, "us-gaap:LiabilitiesCurrent", 2_000 * s4.M)]
    g_conflict = [*s4.filer_g_notes(),
                  s4.note("us-gaap:ContractWithCustomerLiability", 8_700 * s4.M)]
    return {
        "defined": s4.system("Debt", s4.build(s4.filer_h(), s4.filer_h_notes())),
        "not_presented": s4.system("Debt", s4.build(no_debt, [])),
        "unit_mismatch": s4.system("Debt", s4.build(h, [])),
        "conflicting_facts": s4.system("DeferredRevenue", s4.build(s4.filer_g(), g_conflict)),
    }


@pytest.mark.criterion("C6")
def test_precedence_by_status(s4):
    # oracle: requirement 10's table: None and not_presented keep candidate_order, defined
    # answers, unit_mismatch and conflicting_facts refuse
    assert defined_precedence(None) == "candidate_order"
    for status, m in _measures(s4).items():
        assert m["status"] == status
        assert defined_precedence(m) == PRECEDENCE[status]


@pytest.mark.criterion("C6")
def test_screen_member_by_status(s4):
    # oracle: requirement 10 and key-definitions requirement 6: defined -> ranked with the
    # measure (53,400 M, the three line fact ids); not_presented -> metric_not_tagged with
    # nothing; unit_mismatch / conflicting_facts -> excluded with the status as reason
    ms = _measures(s4)
    r = screen_member(ms["defined"])
    assert r["status"] == "ranked"
    assert r["reason"] is None
    assert r["concept"] == "s4:debt_total"
    assert r["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    assert r["fact_ids"] == ms["defined"]["fact_ids"]
    assert len(r["fact_ids"]) == 3
    n = screen_member(ms["not_presented"])
    assert (n["status"], n["reason"], n["concept"], n["value"], n["fact_ids"]) == (
        "metric_not_tagged", None, None, None, [])
    for status in ("unit_mismatch", "conflicting_facts"):
        x = screen_member(ms[status])
        assert (x["status"], x["reason"], x["value"], x["fact_ids"]) == (
            "excluded", status, None, [])


@pytest.mark.criterion("C6")
def test_defined_debt_answers_over_the_candidate_order_winner(s4):
    # oracle: K2 C11: today candidate_order ranks the notes-only primary LongTermDebt (rank 1,
    # 51,400) over the presented lease-inclusive substitute; the defined measure (53,400 =
    # 300 + 4,600 + 48,500 by hand) answers instead
    f = s4.build(s4.filer_h(), s4.filer_h_notes())
    rows = [
        {"concept_role": "substitute", "concept_pref_rank": 54, "period_end": "2026-01-01",
         "presented": True, "value": 48_500 * s4.M, "decimals": -6,
         "fact_id": f.ids["us-gaap:LongTermDebtAndCapitalLeaseObligations"]},
        {"concept_role": "primary", "concept_pref_rank": 1, "period_end": "2026-01-01",
         "presented": False, "value": 51_400 * s4.M, "decimals": -6,
         "fact_id": f.ids["us-gaap:LongTermDebt"]},
    ]
    assert candidate_order(rows)[0] == 1
    m = s4.system("Debt", f)
    assert defined_precedence(m) == "defined"
    assert m["value"] == pytest.approx(53_400 * s4.M, rel=1e-12)  # whole millions: exact
    assert f.ids["us-gaap:LongTermDebt"] not in m["fact_ids"]


@pytest.mark.criterion("C6")
def test_disclosures_carry_the_definition_and_basis(s4):
    # oracle: requirement 8's phrases, per basis (case-insensitive)
    cases = [
        ("Debt", s4.filer_h(), s4.filer_h_notes(), ["total debt", "presented borrowing lines"]),
        ("DeferredRevenue", s4.filer_t(), [], ["total deferred revenue", "as presented"]),
        ("DeferredRevenue", s4.filer_g(), s4.filer_g_notes(),
         ["total deferred revenue", "tagged total", "ContractWithCustomerLiabilityCurrent"]),
        ("DeferredRevenue", s4.filer_o(), s4.filer_o_notes(),
         ["total deferred revenue", "tagged non-current"]),
        ("DeferredRevenue", s4.filer_h(), [], ["total deferred revenue", "no non-current amount"]),
    ]
    for family, lines, notes, phrases in cases:
        text = s4.system(family, s4.build(lines, notes))["disclosure"].lower()
        for phrase in phrases:
            assert phrase.lower() in text, (family, phrase)
    no_debt = [s4.line(s4.TCL, "us-gaap:LiabilitiesCurrent", 2_000 * s4.M)]
    assert "no balance-sheet line" in s4.system("Debt", s4.build(no_debt, []))[
        "disclosure"].lower()


@pytest.mark.criterion("C6")
@given(eur=st.sets(st.integers(0, 9)), conflict=st.booleans(),
       family=st.sampled_from(["Debt", "DeferredRevenue"]),
       drop=st.sets(st.integers(0, 9)))
def test_precedence_and_screen_follow_the_status(s4, eur, conflict, family, drop):
    # oracle: requirements 7 and 10: whatever the filing, the status decides the precedence
    # and the screen status, and only a defined measure carries a value and fact ids
    lines = [d for i, d in enumerate(s4.filer_h()) if i not in drop]
    for i, d in enumerate(lines):
        if i in eur:
            d["unit"] = "EUR"
    notes = s4.filer_h_notes()
    if conflict:
        notes.append(s4.note("us-gaap:ContractWithCustomerLiability", 1_600 * s4.M))
    m = s4.system(family, s4.build(lines, notes))
    assert m["status"] in PRECEDENCE
    assert defined_precedence(m) == PRECEDENCE[m["status"]]
    sm = screen_member(m)
    assert sm["status"] == SCREEN[m["status"]]
    if m["status"] == "defined":
        assert sm["value"] == pytest.approx(m["value"], rel=1e-12)  # the measure's own value
        assert sm["fact_ids"] == m["fact_ids"] != []
    else:
        assert m["value"] is None
        assert sm["value"] is None
        assert m["fact_ids"] == sm["fact_ids"] == []
