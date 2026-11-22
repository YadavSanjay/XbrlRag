"""C1: the live Capex rule strips negating qualifiers; nothing else in the rules moves.

Oracle: requirement 1 of docs/specs/capex-strip-qualifiers.md; the accepted r2.1 copy
(configs/eval/v2/statement_lines.accepted_r2_1.yaml, SHA-256 pinned by witness-rules-r2-1's
record); statement-rules-by-element requirement 3 (a qualifier runs from its cue to the next
",", ";", "(" or ")"), applied by hand to the constructed labels below.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

PPE = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
# Heads the accepted Capex rule takes, and heads it rejects for what they say (none holds a
# cue, so each is its own head).
TAKEN = ["Purchases of property and equipment", "Capital expenditures",
         "Payments for property, plant and equipment", "Additions to property and equipment"]
REJECTED = ["Proceeds from sale of property and equipment",
            "Purchases of property and equipment included in accounts payable",
            "Accrued capital expenditures",
            "Capital expenditures incurred but not yet paid",
            "Purchases of property and equipment under finance leases"]
CUES = ["excluding", "exclusive of", "not including", "other than", "except", "net of"]
# Qualifier tails naming what the accepted rule's exclusions name (leases, payables,
# accruals, financing, non-cash), none holding a terminator.
TAILS = ["finance leases", "amounts in accounts payable", "accrued purchases",
         "financing arrangements", "non-cash additions", "sales"]
FORMS = ["{h} {c} {t}", "{h}, {c} {t}", "{h} ({c} {t})", "{h}; {c} {t}"]


@pytest.mark.criterion("C1")
def test_live_capex_is_the_accepted_rule_plus_strip_qualifiers(kit):
    # oracle: requirement 1 - Capex is the accepted r2.1 rule with `strip_qualifiers: true`;
    # every other judged family is the accepted r2.1 rule; Debt and DeferredRevenue are the
    # accepted copy's, key-definitions' or debt-ifrs-elements' locked entries; no other family
    # strips
    assert kit.sha(kit.accepted_r2_1) == kit.accepted_r2_1_sha
    e = kit.loaded()
    assert e.live["Capex"] == {**e.accepted["Capex"], "strip_qualifiers": True}
    assert set(e.live) == set(e.accepted)
    for f in e.live:
        if f in kit.unjudged:
            assert e.live[f] in (e.accepted[f], e.kd[f], e.debt[f]), f
        elif f != "Capex":
            assert e.live[f] == e.accepted[f], f
    assert [f for f, r in e.live.items() if r.get("strip_qualifiers")] == ["Capex"]


@pytest.mark.criterion("C1")
def test_qualified_capex_labels_point(kit):
    # oracle: requirement 2 by hand - "Capital expenditures, net of related payables" (head
    # "Capital expenditures") and "Purchases of property and equipment, excluding amounts in
    # accounts payable" (head "Purchases of property and equipment") are candidates; the
    # accepted rule rejects both ("\bpayable"); "Principal payments on debt, excluding
    # amortization of rental property debt" (head "Principal payments on debt") is no longer
    # one; "Accrued capital expenditures, net of payments" stays rejected (its head accrues)
    from src.eval.keys.statement_witness import line_is_candidate

    e = kit.loaded()
    live, old = e.live["Capex"], e.accepted["Capex"]
    for label in ("Capital expenditures, net of related payables",
                  "Purchases of property and equipment, excluding amounts in accounts payable"):
        assert line_is_candidate(old, label, PPE) is False, label
        assert line_is_candidate(live, label, PPE) is True, label
    debt = "Principal payments on debt, excluding amortization of rental property debt"
    assert line_is_candidate(old, debt, PPE) is True
    assert line_is_candidate(live, debt, PPE) is False
    assert line_is_candidate(live, "Accrued capital expenditures, net of payments", PPE) is False


@pytest.mark.criterion("C1")
@given(head=st.sampled_from(TAKEN + REJECTED), cue=st.sampled_from(CUES),
       tail=st.sampled_from(TAILS), form=st.sampled_from(FORMS))
def test_a_qualifier_never_decides_a_capex_line(kit, head, cue, tail, form):
    # oracle: requirement 2 (property) - for a head the accepted rule takes, the head plus
    # any negating qualifier (any cue, any of four forms, a tail naming an excluded term) is
    # a candidate; for a head it rejects, it stays rejected: only the head decides
    from src.eval.keys.statement_witness import line_is_candidate

    live = kit.loaded().live["Capex"]
    label = form.format(h=head, c=cue, t=tail)
    assert line_is_candidate(live, label, PPE) is (head in TAKEN)
    assert line_is_candidate(live, head, PPE) is (head in TAKEN)
