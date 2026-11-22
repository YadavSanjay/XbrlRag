"""C4: on the same facts the system's defined measure is the key side's (requirement 9)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.key_definitions import member_status
from src.retriever.xbrl.defined_measure import screen_member

# Hand sums (M) of the fixture filers, from conftest's descriptions.
HAND = {
    ("H", "Debt"): 53_400, ("H", "DeferredRevenue"): 2_600,
    ("G", "Debt"): 46_500, ("G", "DeferredRevenue"): 8_600,
    ("T", "Debt"): 8_300, ("T", "DeferredRevenue"): 7_000,
    ("O", "Debt"): 92_300, ("O", "DeferredRevenue"): 10_500,
}

# Line templates in accounting vocabulary: (label, concept), one concept each.
CURRENT = [
    ("Short-term borrowings", "us-gaap:CommercialPaper"),
    ("Current portion of long-term debt", "us-gaap:LongTermDebtCurrent"),
    ("Current portion of debt and finance leases", "ext:CurrentPortionOfDebtAndFinanceLeases"),
    ("Notes payable", "us-gaap:NotesPayableCurrent"),
    ("Deferred revenue", "us-gaap:ContractWithCustomerLiabilityCurrent"),
    ("Unearned revenue", "us-gaap:DeferredRevenueCurrent"),
    ("Customer deposits and contract liabilities", "ext:ContractLiabilitiesAndDepositsCurrent"),
    ("Accounts payable", "us-gaap:AccountsPayableCurrent"),
    ("Accrued interest", "us-gaap:InterestPayableCurrent"),
    ("Operating lease liabilities, current", "us-gaap:OperatingLeaseLiabilityCurrent"),
]
NONCURRENT = [
    ("Long-term debt", "us-gaap:LongTermDebtNoncurrent"),
    ("Long-term debt and finance leases", "us-gaap:LongTermDebtAndCapitalLeaseObligations"),
    ("Senior notes", "us-gaap:SeniorNotes"),
    ("Debt and finance leases, net of current portion",
     "ext:DebtAndFinanceLeasesNetOfCurrentPortion"),
    ("Deferred revenue, non-current", "us-gaap:ContractWithCustomerLiabilityNoncurrent"),
    ("Long-term unearned revenue", "us-gaap:DeferredRevenueNoncurrent"),
    ("Deferred revenues - franchise fees", "ext:DeferredRevenuesFranchiseFees"),
    ("Deferred income taxes", "us-gaap:DeferredIncomeTaxLiabilitiesNet"),
    ("Operating lease liabilities", "us-gaap:OperatingLeaseLiabilityNoncurrent"),
    ("Total debt", "us-gaap:DebtInstrumentCarryingAmount"),
]
NOTE_CONCEPTS = [
    "us-gaap:DebtLongtermAndShorttermCombinedAmount", "us-gaap:LongTermDebt",
    "us-gaap:ContractWithCustomerLiability", "us-gaap:DeferredRevenue",
    "us-gaap:ContractWithCustomerLiabilityNoncurrent", "us-gaap:DeferredRevenueNoncurrent",
]

values = st.integers(1, 100_000) | st.just("nil")
filings = st.fixed_dictionaries({
    "current": st.lists(st.tuples(st.sampled_from(range(len(CURRENT))), values),
                        max_size=6, unique_by=lambda t: t[0]),
    "noncurrent": st.lists(st.tuples(st.sampled_from(range(len(NONCURRENT))), values),
                           max_size=6, unique_by=lambda t: t[0]),
    "tcl": st.booleans(),
    "notes": st.lists(st.tuples(st.sampled_from(range(len(NOTE_CONCEPTS))), values),
                      max_size=6, unique_by=lambda t: t[0]),
})


def _build(s4, d):
    def val(v):
        return s4.NIL if v == "nil" else v * s4.M

    lines = [s4.line(*CURRENT[i], val(v)) for i, v in d["current"]]
    if d["tcl"]:
        lines.append(s4.line(s4.TCL, "us-gaap:LiabilitiesCurrent", 999_999 * s4.M))
    lines += [s4.line(*NONCURRENT[i], val(v)) for i, v in d["noncurrent"]]
    on_lines = {ln["concept"] for ln in lines}
    notes = [s4.note(NOTE_CONCEPTS[i], val(v)) for i, v in d["notes"]
             if NOTE_CONCEPTS[i] not in on_lines]
    return s4.build(lines, notes)


def _summed_concepts(k) -> list[str]:
    """The key components whose values make up the key value (its note one for note_total)."""
    if k["basis"] == "note_total":
        return [c["concept"] for c in k["components"] if c["section"] == "note"]
    return [c["concept"] for c in k["components"]]


def _same_answer(m, k) -> None:
    assert m["status"] == k["status"]
    assert m["basis"] == k["basis"]
    if k["value"] is None:
        assert m["value"] is None
    else:
        assert m["value"] == pytest.approx(k["value"], rel=1e-12)  # same facts, same sum order
    assert [p["concept"] for p in m["parts"]] == _summed_concepts(k)
    assert [(r["reading"], r["concept"]) for r in m["readings"]] == [
        (r["reading"], r["concept"]) for r in k["readings"]]
    for mine, theirs in zip(m["readings"], k["readings"], strict=True):
        assert mine["value"] == pytest.approx(theirs["value"], rel=1e-12)  # same tagged value
    sm, km = screen_member(m), member_status(k)
    assert sm["status"] == km["status"]
    if km["value"] is None:
        assert sm["value"] is None
    else:
        assert sm["value"] == pytest.approx(km["value"], rel=1e-12)  # the same member value


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("filer,family", sorted(HAND))
def test_fixture_filers_agree_with_the_key_and_the_hand_sum(s4, filer, family):
    # oracle: the hand sums in HAND (conftest's line values added by hand) and the key side's
    # debt_key / deferred_revenue_key run on the FSDS and companyfacts form of the same facts
    lines, notes = {
        "H": (s4.filer_h, s4.filer_h_notes), "G": (s4.filer_g, s4.filer_g_notes),
        "T": (s4.filer_t, s4.filer_t_notes), "O": (s4.filer_o, s4.filer_o_notes),
    }[filer]
    f = s4.build(lines(), notes())
    k = s4.key(family, f)
    m = s4.system(family, f)
    assert k["value"] == pytest.approx(HAND[(filer, family)] * s4.M, rel=1e-12)  # hand sum
    assert m["value"] == pytest.approx(HAND[(filer, family)] * s4.M, rel=1e-12)  # hand sum
    _same_answer(m, k)


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(d=filings, family=st.sampled_from(["Debt", "DeferredRevenue"]))
def test_any_filing_gives_the_key_sides_answer(s4, d, family):
    # oracle: the key-side functions (key-definitions requirements 2, 3 and 6, implemented and
    # locked) on the same facts: status, basis, value, summed parts, readings, screen member
    f = _build(s4, d)
    k = s4.key(family, f)
    assert k["status"] in ("defined", "not_presented")
    _same_answer(s4.system(family, f), k)
