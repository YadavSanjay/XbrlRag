"""C7: a mapping change can move a key only by raising.

``select_key`` receives the mapping's candidate keys; re-ranking them, adding a component the
rank-50 band no longer holds back, or removing a candidate that is not the chosen one must not
change the chosen witness. Oracle: the unperturbed selection over the same fixtures, compared
as canonical JSON (byte-identical), and the hand-checked statement lines it rests on.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import WitnessBlocked, select_key
from src.eval.suites.v2_suite import canonical_json

# tolerance: filed integer amounts; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
REV = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
Y23 = ("2023-12-31", "2023-01-01")
Y24 = ("2024-12-31", "2024-01-01")
DEBT = ("us-gaap:LongTermDebt", "us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtCurrent")


def _groups(kit):
    """family, annual adsh, candidates (the chosen one first), annual values. Hand-checked:
    VST "Operating revenues" 14,779 (2023); MCD cash-flow D&A 2,097 (2024); AAPL "Term debt"
    12,350 + 78,328 = 90,678 = the 10-K's LongTermDebt (2025-09-27)."""
    k = kit.key
    vst = [k("VST", "us-gaap:Revenues", kit.VST, *Y23, 2023), k("VST", REV, kit.VST, *Y23, 2023)]
    mcd = [k("MCD", "us-gaap:DepreciationAndAmortization", kit.MCD, *Y24, 2024),
           k("MCD", "us-gaap:DepreciationDepletionAndAmortization", kit.MCD, *Y24, 2024)]
    aapl = [k("AAPL", c, kit.AAPL, "2025-09-27", None, 2025) for c in DEBT]
    return {"vst": ("Revenue", kit.VST, vst, _vals(vst), 14_779e6, "agree"),
            "mcd": ("DepreciationAndAmortization", kit.MCD, mcd, _vals(mcd), 2_097e6, "agree"),
            "aapl": ("Debt", kit.AAPL, aapl, _vals(aapl), 90_678e6, "agree_other_element")}


def _vals(keys):
    """The annual values: every candidate here is keyed from the annual 10-K itself."""
    return {k["concept"]: k["value"] for k in keys}


def _select(kit, family, adsh, cands, vals):
    e = kit.env()
    return select_key(e.tables, e.rules, e.notes, e.resolutions, family, cands, adsh, vals)


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("name", ["vst", "mcd", "aapl"])
def test_the_statement_line_chooses_the_key(kit, name):
    # oracle: the hand-checked lines in _groups' docstring
    family, adsh, cands, vals, value, status = _groups(kit)[name]
    got = _select(kit, family, adsh, cands, vals)
    assert got["key_concept"] == cands[0]["concept"]
    assert got["status"] == status
    assert got["value"] == pytest.approx(value, rel=REL)
    assert got["key_value"] == pytest.approx(value, rel=REL)


@pytest.mark.criterion("C7")
@pytest.mark.parametrize("name", ["vst", "mcd"])
def test_removing_the_chosen_candidate_raises(kit, name):
    # oracle: requirement 7: what is left are components the statement disagrees with
    family, adsh, cands, vals, _v, _s = _groups(kit)[name]
    with pytest.raises(WitnessBlocked):
        _select(kit, family, adsh, cands[1:], vals)


@pytest.mark.criterion("C7")
@given(name=st.sampled_from(["vst", "mcd", "aapl"]),
       seed=st.integers(min_value=0, max_value=2**32 - 1),
       keep=st.integers(min_value=0, max_value=3))
def test_reranking_adding_and_removing_components_changes_nothing(kit, name, seed, keep):
    # oracle: the unperturbed selection (canonical JSON, byte-identical), requirement 7
    family, adsh, cands, vals, _v, _s = _groups(kit)[name]
    base = canonical_json(_select(kit, family, adsh, cands, vals))
    rng = random.Random(seed)
    others = cands[1:]
    kept = rng.sample(others, min(keep, len(others)))           # remove some components
    extra = dict(cands[1], concept="us-gaap:SomeComponent", value=cands[0]["value"] * 0.5)
    extra_vals = dict(vals, **{"us-gaap:SomeComponent": extra["value"]})
    perturbed = [cands[0], *kept, extra]
    rng.shuffle(perturbed)                                      # re-rank
    assert canonical_json(_select(kit, family, adsh, perturbed, extra_vals)) == base


@pytest.mark.criterion("C7")
@pytest.mark.metamorphic
def test_two_accepted_candidates_that_disagree_raise(kit):
    # oracle: requirement 3 (select_key): accepted candidates with values that are not the
    # same raise; TSLA's Revenues and RevenueFromContract... both equal "Total revenues"
    # 96,773 (2023), and the element on the line wins
    tsla = [kit.key("TSLA", REV, kit.TSLA, *Y23, 2023),
            kit.key("TSLA", "us-gaap:Revenues", kit.TSLA, *Y23, 2023)]
    got = _select(kit, "Revenue", kit.TSLA, list(reversed(tsla)), _vals(tsla))
    assert (got["key_concept"], got["status"]) == (REV, "agree")
    note_key = kit.key("META", "us-gaap:InvestmentIncomeInterest", kit.META, "2025-12-31",
                       "2025-01-01", 2025)
    # constructed: a second listed note concept with another value
    other = dict(note_key, concept="us-gaap:InvestmentIncomeInterestAndDividend", value=2_000e6)
    with pytest.raises(WitnessBlocked):
        _select(kit, "InterestIncome", kit.META, [note_key, other], {})
