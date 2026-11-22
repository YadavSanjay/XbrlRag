"""Spec tests for concept-rank-integrity: two-tier family rankings and per-filer
rankings that replace, rather than tie with, the generic rank 1
(docs/specs/concept-rank-integrity.md).

Pure: `family_ranking` and `filer_ranking` on QName lists; no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.ingest.sec_edgar.ingest.layer0_populator import filer_ranking
from src.ingest.util.generate_concept_mapping import family_ranking

COMPONENT_BASE = 50  # the spec's component band starts here
IFRS_BASE = 100  # IFRS rows keep their own band, which stays eligible


# ------------------------------------------------------------------ oracles (the rules)
def _family_oracle(primaries, components, descendants):
    """C1 written out: primaries, then new descendants, then new components from 50."""
    out, seen = [], set()
    for q in [*primaries, *descendants]:
        if q not in seen:
            seen.add(q)
            out.append((q, len(out) + 1))
    if len(out) >= COMPONENT_BASE:
        raise ValueError("primaries and descendants reach the component band")
    rank = COMPONENT_BASE
    for q in components:
        if q not in seen:
            seen.add(q)
            out.append((q, rank))
            rank += 1
    return out


def _eligible(rank):
    return rank < COMPONENT_BASE or rank >= IFRS_BASE


def _filer_oracle(global_ranking, reported):
    """C2 written out."""
    ordered = sorted(global_ranking, key=lambda qr: qr[1])
    eligible = [q for q, r in ordered if _eligible(r)]
    winner = next((q for q in eligible if q in reported), None)
    if winner is None or winner == eligible[0]:
        return None
    rest = [q for q in eligible if q != winner]
    out = [(winner, 1)] + [(q, i) for i, q in enumerate(rest, start=2)]
    return out + [(q, r) for q, r in ordered if not _eligible(r)]


def _as_set(ranking):
    return None if ranking is None else set(ranking)


# --------------------------------------------------------------------------- C1
DA_PRIMARIES = ["us-gaap:DepreciationDepletionAndAmortization",
                "us-gaap:DepreciationAndAmortization",
                "us-gaap:DepreciationAmortizationAndOther"]
DA_COMPONENTS = ["us-gaap:Depreciation", "us-gaap:AmortizationOfIntangibleAssets"]


@pytest.mark.criterion("C1")
def test_family_ranking_point_cases():
    # oracle: the definition, by hand on the D&A family of the spec's table
    assert family_ranking(DA_PRIMARIES, DA_COMPONENTS, []) == [
        ("us-gaap:DepreciationDepletionAndAmortization", 1),
        ("us-gaap:DepreciationAndAmortization", 2),
        ("us-gaap:DepreciationAmortizationAndOther", 3),
        ("us-gaap:Depreciation", 50),
        ("us-gaap:AmortizationOfIntangibleAssets", 51),
    ]
    # Descendants follow the primaries; a QName already listed keeps its first place.
    assert family_ranking(["a:P1", "a:P2", "a:P1"], ["a:C1", "a:P2"], ["a:D1", "a:P1"]) == [
        ("a:P1", 1), ("a:P2", 2), ("a:D1", 3), ("a:C1", 50)]
    # 49 primaries fit below the band; 50 do not.
    assert family_ranking([f"a:P{i}" for i in range(49)], ["a:C"], [])[-1] == ("a:C", 50)
    with pytest.raises(ValueError):
        family_ranking([f"a:P{i}" for i in range(48)], [], ["a:D1", "a:D2"])


names = st.sampled_from([f"a:Q{i}" for i in range(12)])


@pytest.mark.criterion("C1")
@given(st.lists(names, min_size=1, max_size=8), st.lists(names, max_size=5),
       st.lists(names, max_size=5))
def test_family_ranking_matches_the_rule(primaries, components, descendants):
    # oracle: C1 written out (`_family_oracle`)
    assert family_ranking(primaries, components, descendants) == _family_oracle(
        primaries, components, descendants)


# --------------------------------------------------------------------------- C2
NI_GLOBAL = [
    ("us-gaap:NetIncomeLoss", 1),
    ("us-gaap:ProfitLoss", 2),
    ("us-gaap:NetIncomeLossAttributableToParent", 3),
    ("ifrs-full:ProfitLoss", 100),
    ("ifrs-full:ProfitLossAttributableToOwnersOfParent", 101),
]
DEBT_GLOBAL = [
    ("us-gaap:LongTermDebt", 1),
    ("us-gaap:LongTermDebtNoncurrent", 2),
    ("us-gaap:LongTermDebtAndCapitalLeaseObligations", 3),
    ("us-gaap:DebtCurrent", 50),
    ("us-gaap:ShortTermBorrowings", 51),
    ("us-gaap:NotesPayable", 52),
]
DA_GLOBAL = [
    ("us-gaap:DepreciationDepletionAndAmortization", 1),
    ("us-gaap:DepreciationAndAmortization", 2),
    ("us-gaap:DepreciationAmortizationAndOther", 3),
    ("us-gaap:Depreciation", 50),
    ("us-gaap:AmortizationOfIntangibleAssets", 51),
]


@pytest.mark.criterion("C2")
def test_filer_ranking_point_cases():
    # oracle: the definition, by hand on the 2026-10-14 filers
    # Oracle reports the global rank 1: no override.
    assert filer_ranking(NI_GLOBAL, {"us-gaap:NetIncomeLoss", "us-gaap:ProfitLoss"}) is None
    # TSMC reports only the IFRS concept: it moves to 1, the rest follow in order.
    assert filer_ranking(NI_GLOBAL, {"ifrs-full:ProfitLoss"}) == [
        ("ifrs-full:ProfitLoss", 1),
        ("us-gaap:NetIncomeLoss", 2),
        ("us-gaap:ProfitLoss", 3),
        ("us-gaap:NetIncomeLossAttributableToParent", 4),
        ("ifrs-full:ProfitLossAttributableToOwnersOfParent", 5),
    ]
    # AMD reports a primary and a component: the primary wins, components stay put.
    assert filer_ranking(
        DEBT_GLOBAL, {"us-gaap:LongTermDebtNoncurrent", "us-gaap:ShortTermBorrowings"}) == [
        ("us-gaap:LongTermDebtNoncurrent", 1),
        ("us-gaap:LongTermDebt", 2),
        ("us-gaap:LongTermDebtAndCapitalLeaseObligations", 3),
        ("us-gaap:DebtCurrent", 50),
        ("us-gaap:ShortTermBorrowings", 51),
        ("us-gaap:NotesPayable", 52),
    ]
    # Alphabet reports only D&A components: a component never becomes rank 1.
    assert filer_ranking(
        DA_GLOBAL, {"us-gaap:Depreciation", "us-gaap:AmortizationOfIntangibleAssets"}) is None
    # Nothing reported at all.
    assert filer_ranking(DEBT_GLOBAL, set()) is None


@st.composite
def ranking_case(draw):
    """A global ranking with primaries, components and IFRS rows, plus a reported set."""
    n_p = draw(st.integers(min_value=1, max_value=5))
    n_c = draw(st.integers(min_value=0, max_value=3))
    n_i = draw(st.integers(min_value=0, max_value=2))
    ranking = ([(f"p:P{i}", i + 1) for i in range(n_p)]
               + [(f"c:C{i}", COMPONENT_BASE + i) for i in range(n_c)]
               + [(f"i:I{i}", IFRS_BASE + i) for i in range(n_i)])
    reported = draw(st.sets(st.sampled_from([q for q, _ in ranking])))
    return ranking, reported


@st.composite
def override_case(draw):
    """A case whose filer does not report the global best but reports another eligible
    QName, so an override is due by construction."""
    ranking, reported = draw(ranking_case())
    eligible = [q for q, r in ranking if _eligible(r)]
    if len(eligible) < 2:
        ranking = [*ranking, ("p:Pextra", len([r for _, r in ranking if r < 50]) + 1)]
        eligible = [q for q, r in sorted(ranking, key=lambda t: t[1]) if _eligible(r)]
    pick = draw(st.sampled_from(eligible[1:]))
    reported = (set(reported) - {eligible[0]}) | {pick}
    return ranking, reported


@pytest.mark.criterion("C2")
@given(override_case(), ranking_case())
def test_filer_ranking_matches_the_rule(due, any_case):
    # oracle: C2 written out (`_filer_oracle`); the first case needs an override by
    # construction, the second may go either way
    ranking, reported = due
    assert filer_ranking(ranking, reported) == _filer_oracle(ranking, reported)
    assert filer_ranking(ranking, reported) is not None
    ranking, reported = any_case
    assert filer_ranking(ranking, reported) == _filer_oracle(ranking, reported)


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(override_case(), st.randoms(use_true_random=False))
def test_filer_ranking_properties(case, rnd):
    # oracle: the C3 properties, checked directly
    ranking, reported = case
    out = filer_ranking(ranking, reported)
    assert out is not None
    assert sorted(q for q, _ in out) == sorted(q for q, _ in ranking)  # a permutation
    assert len({r for _, r in out}) == len(out)  # distinct ranks
    glob = dict(ranking)
    assert all(r == glob[q] for q, r in out if not _eligible(glob[q]))  # components fixed
    winner = next(q for q, r in out if r == 1)
    assert winner in reported
    assert not any(q in reported and _eligible(r) and r < glob[winner] for q, r in ranking)
    assert filer_ranking(out, reported) is None  # idempotent
    shuffled = list(ranking)
    rnd.shuffle(shuffled)
    assert _as_set(filer_ranking(shuffled, set(reported))) == set(out)


@pytest.mark.criterion("C3")
def test_filer_ranking_properties_point_case():
    # oracle: the C3 properties on the TSMC case — reapplying is a no-op, input order
    # does not matter
    out = filer_ranking(NI_GLOBAL, {"ifrs-full:ProfitLoss"})
    assert out is not None and out[0] == ("ifrs-full:ProfitLoss", 1)
    assert filer_ranking(out, {"ifrs-full:ProfitLoss"}) is None
    assert set(filer_ranking(list(reversed(NI_GLOBAL)), ["ifrs-full:ProfitLoss"])) == set(out)


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.lists(names, min_size=1, max_size=8, unique=True), st.lists(names, max_size=5),
       st.lists(names, max_size=5))
def test_family_ranking_properties(primaries, components, descendants):
    # oracle: the C4 properties, checked directly
    out = family_ranking(primaries, components, descendants)
    ranks = [r for _, r in out]
    assert len(set(ranks)) == len(ranks)
    assert out[:len(primaries)] == [(q, i) for i, q in enumerate(primaries, start=1)]
    assert all(r >= COMPONENT_BASE for q, r in out
               if q in components and q not in primaries and q not in descendants)
    assert sorted(q for q, _ in out) == sorted({*primaries, *components, *descendants})


@pytest.mark.criterion("C4")
def test_family_ranking_properties_point_case():
    # oracle: the C4 properties on the D&A family
    out = family_ranking(DA_PRIMARIES, DA_COMPONENTS, [])
    assert [r for _, r in out] == [1, 2, 3, 50, 51]
    assert {q for q, _ in out} == {*DA_PRIMARIES, *DA_COMPONENTS}
