"""C2: a fallback the witness blocks stays held unless a reviewed resolution covers it.

Oracle: requirement 2 of docs/specs/key-fallback-witness.md and the conftest's rows: FALL's
FY2024 10-K presents "Interest income and other, net" 169 on InterestAndOtherIncome (the
InterestIncome rule excludes "other"), and its D&A line is the 1,000 of
DepreciationDepletionAndAmortization, not the 800 of Depreciation.
"""

from __future__ import annotations

from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import (
    WitnessBlocked,
    resolve,
    select_key,
    statement_witness,
    witness_manifest,
)
from src.eval.suites.v2_witness import WitnessDrop

# tolerance: whole-million fixture amounts; rel 1e-12 absorbs only binary floating point.
REL = 1e-12
INT = "us-gaap:InterestAndOtherIncome"
DEP = "us-gaap:Depreciation"
DDA = "us-gaap:DepreciationDepletionAndAmortization"
FIN = "us-gaap:NetCashProvidedByUsedInFinancingActivities"
CAPEX = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"


def _key(kit, concept, millions, accession, year=2024):
    start, end = kit.period(year)
    return {"concept": concept, "unit": "USD", "period_start": start, "period_end": end,
            "value": float(millions * kit.M), "accession": accession, "form": "10-K",
            "cik": kit.CIK["FALL"], "fiscal_year": year}


def _fy_key(w, kit, family, concept, year=2024):
    return w.fy_key(kit.companyfacts("FALL"), kit.labels("FALL"), kit.CIK["FALL"], family,
                    kit.spec(concept), year)


@pytest.mark.criterion("C2")
def test_an_unresolved_blocked_fallback_drops_as_held_without_a_block(kit):
    # oracle: FALL FY2024 InterestAndOtherIncome 169 from the unpublished FY2025 10-K; the own
    # report's line "Interest income and other, net" is rejected by the rule (rejected_line,
    # blocking) and no resolution covers it: held as fallback_blocked, never a build block
    w = kit.witness(resolutions="none")
    with pytest.raises(WitnessDrop) as err:
        _fy_key(w, kit, "InterestIncome", INT)
    assert err.value.args == ("fallback_blocked",)
    assert w.blocked == []
    w.raise_if_blocked()
    m = w.manifest()
    assert m["by_status"]["rejected_line"] == 1
    assert m["held"] == [{"family": "InterestIncome", "cik": kit.CIK["FALL"],
                          "fiscal_year": 2024, "key_concept": INT, "status": "rejected_line"}]
    assert m.get("fallback") == {"n": 1, "held": 1}


@pytest.mark.criterion("C2")
def test_a_reviewed_resolution_decides_a_blocked_fallback(kit):
    # oracle: fixtures/key_resolutions_drop.yaml and _key.yaml cover (InterestIncome, FALL,
    # 2024, InterestAndOtherIncome, 169): drop -> resolved_drop; key -> keyed 169 from the
    # FY2025 10-K (S1), witnessed on the FY2024 10-K
    dropped = kit.witness(resolutions="drop")
    with pytest.raises(WitnessDrop) as err:
        _fy_key(dropped, kit, "InterestIncome", INT)
    # grading-dont-care-and-ties requirement 2: the reviewed value may ride along as args[1]
    assert err.value.args[0] == "resolved_drop"
    assert err.value.args[1:] in ((), (pytest.approx(169e6, rel=REL),))
    kept = kit.witness(resolutions="key")
    key, adsh, annual_value = _fy_key(kept, kit, "InterestIncome", INT)
    assert (key["concept"], key["accession"], adsh) == (INT, kit.FA25, kit.FA24)
    assert key["value"] == pytest.approx(169e6, rel=REL)
    assert annual_value == pytest.approx(169e6, rel=REL)
    m = kept.manifest()
    assert m["resolved"] == 1 and m.get("fallback") == {"n": 1, "held": 0}


@pytest.mark.criterion("C2")
def test_the_manifest_holds_a_blocked_fallback_and_still_refuses_a_plain_block(kit):
    # oracle: requirement 2: an unresolved blocking witness with the fallback field is held;
    # the same record without the field is an ordinary unresolved block (WitnessBlocked)
    e = kit.env()
    w = resolve(statement_witness(kit.all_tables(), e.rules, e.notes, "InterestIncome",
                                  _key(kit, INT, 169, kit.FA25), kit.FA24, 169.0 * kit.M),
                e.resolutions["none"])
    assert (w["status"], w["reason"], w.get("fallback")) == (
        "rejected_line", "Interest income and other, net", "own_annual_report")
    m = witness_manifest([w])
    assert (m["n"], m["by_status"]["rejected_line"], m.get("fallback")) == (
        1, 1, {"n": 1, "held": 1})
    plain = {f: v for f, v in w.items() if f != "fallback"}
    with pytest.raises(WitnessBlocked):
        witness_manifest([plain])


@pytest.mark.criterion("C2")
def test_select_key_prefers_the_presented_candidate_over_a_held_first_concept(kit):
    # oracle: FALL FY2024 D&A: Depreciation 800 disagrees with the own report's 1,000 line,
    # DepreciationDepletionAndAmortization 1,000 agrees; both keys come from the unpublished
    # FY2025 10-K. Today both were no_fsds and the alphabetically first (Depreciation) was held.
    e = kit.env()
    cands = [_key(kit, DEP, 800, kit.FA25), _key(kit, DDA, 1000, kit.FA25)]
    w = select_key(kit.all_tables(), e.rules, e.notes, e.resolutions["none"],
                   "DepreciationAndAmortization", cands, kit.FA24,
                   {DEP: 800.0 * kit.M, DDA: 1000.0 * kit.M})
    assert (w["status"], w["key_concept"], w["adsh"], w.get("fallback")) == (
        "agree", DDA, kit.FA24, "own_annual_report")
    assert w["key_value"] == pytest.approx(1000e6, rel=REL)
    wit = kit.witness(candidates={(kit.CIK["FALL"], 2024, "DepreciationAndAmortization"): [DDA]})
    key, adsh, _v = _fy_key(wit, kit, "DepreciationAndAmortization", DEP)
    assert (key["concept"], key["accession"], adsh) == (DDA, kit.FA25, kit.FA24)


# (family, key args, annual value in millions or None, hand status, fallback?, held?)
LABELLED = [
    ("FinancingCashFlow", (FIN, -10764, "FA25"), -10764, "agree", True, False),
    ("Capex", (CAPEX, 520, "FA25"), 500, "value_recast", True, True),
    ("InterestIncome", (INT, 169, "FA25"), 169, "rejected_line", True, True),
    ("DepreciationAndAmortization", (DEP, 800, "FA25"), 800, "disagree", True, True),
    ("DepreciationAndAmortization", (DDA, 1000, "FA25"), 1000, "agree", True, False),
    ("FinancingCashFlow", (FIN, -10764, "FA24"), -10764, "agree", False, False),
    ("FinancingCashFlow", (FIN, -9000, "FA25", 2025), -9000, "no_fsds", False, True),
]
_ALL: list = []


def _labelled(kit):
    if not _ALL:
        e = kit.env()
        for family, (concept, millions, acc, *year), annual, _s, _f, _h in LABELLED:
            y = year[0] if year else 2024
            k = _key(kit, concept, millions, getattr(kit, acc), year=y)
            own = kit.FA24 if y == 2024 else kit.FA25
            _ALL.append(resolve(statement_witness(kit.all_tables(), e.rules, e.notes, family, k,
                                                  own, float(annual * kit.M)),
                                e.resolutions["none"]))
    return _ALL


@pytest.mark.criterion("C2")
@given(picks=st.lists(st.integers(min_value=0, max_value=len(LABELLED) - 1), max_size=20))
def test_fallback_counts_of_any_selection_are_the_hand_counts(kit, picks):
    # oracle: the hand labels of LABELLED (status, fallback read, held), counted for any
    # multiset; no pick is an unresolved plain block, so the manifest never raises
    ws = _labelled(kit)
    assert [w["status"] for w in ws] == [row[3] for row in LABELLED]
    m = witness_manifest([ws[i] for i in picks])
    rows = [LABELLED[i] for i in picks]
    assert m["n"] == len(picks)
    assert m.get("fallback") == {"n": sum(1 for r in rows if r[4]),
                             "held": sum(1 for r in rows if r[4] and r[5])}
    want = Counter(r[3] for r in rows)
    assert {s: n for s, n in m["by_status"].items() if n} == dict(want)
    assert len(m["held"]) == sum(1 for r in rows if r[5])
