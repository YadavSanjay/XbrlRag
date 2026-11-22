"""C4: a key with no presented line is accepted only on a reviewed note concept.

Oracle: the us-gaap 2025 element definitions (committed excerpt ``taxonomy_excerpt.json`` of
the FASB documentation and label linkbases, SHA-256 in provenance.json) and the fixtures'
statements, checked by hand (no line for the family).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_note_concepts, statement_witness

NOTES_SHA256 = "1176e22ebaea545ea57e86194c6cfba32e9cf0df561ae249675aaf9a30939095"
ETR = "us-gaap:EffectiveIncomeTaxRateContinuingOperations"
STATUTORY = "us-gaap:EffectiveIncomeTaxRateReconciliationAtFederalStatutoryIncomeTaxRate"
III = "us-gaap:InvestmentIncomeInterest"


def _note_keys(kit):
    """(family, key, annual adsh), each hand-checked: the statement presents no family line."""
    k = kit.key
    return [
        # oracle: ABT FY2025 10-K: no rate line on the income statement; tax note "Effective
        # tax rate on earnings" 2023: 14.1%
        ("EffectiveTaxRate",
         k("ABT", ETR, kit.ABT, "2023-12-31", "2023-01-01", 2023, unit="pure"), kit.ABT),
        # oracle: META FY2025 10-K income statement "Interest and other income, net"; note
        # "Interest income" 2024: 2,517, 2025: 2,123
        ("InterestIncome", k("META", III, kit.META, "2024-12-31", "2024-01-01", 2024), kit.META),
        ("InterestIncome", k("META", III, kit.META, "2025-12-31", "2025-01-01", 2025), kit.META),
        # oracle: INTC FY2025 10-K income statement "Interest and other, net"; note "Interest
        # income" 2023: 1,335
        ("InterestIncome", k("INTC", III, kit.INTC, "2023-12-30", "2023-01-01", 2023), kit.INTC),
        # oracle: KO FY2025 10-K balance sheet "Accounts payable and accrued expenses"
        # (broader); note 8 "Accounts payable" 5,649
        ("AccountsPayable",
         k("KO", "us-gaap:AccountsPayableTradeCurrent", kit.KO, "2025-12-31", None, 2025), kit.KO),
    ]


@pytest.mark.criterion("C4")
def test_listed_note_concepts_are_witnessed(kit):
    # oracle: each key's hand check in _note_keys; each concept's us-gaap definition is the
    # family's measure (taxonomy_excerpt.json)
    e = kit.env()
    for family, k, adsh in _note_keys(kit):
        w = statement_witness(e.tables, e.rules, e.notes, family, k, adsh)
        got = (w["status"], w["lines"], w["value"])
        assert got == ("note_witnessed", [], None), (family, k["concept"])


@pytest.mark.criterion("C4")
def test_statutory_rate_is_unlisted(kit):
    # oracle: us-gaap documentation of the statutory-rate element, "Percentage of domestic
    # federal statutory tax rate applicable to pretax income", a different measure from the
    # effective rate; ABT's 2023 value 0.21
    e = kit.env()
    assert "statutory" in kit.taxonomy["elements"][STATUTORY]["documentation"]
    k = kit.key("ABT", STATUTORY, kit.ABT, "2023-12-31", "2023-01-01", 2023, unit="pure")
    w = statement_witness(e.tables, e.rules, e.notes, "EffectiveTaxRate", k, kit.ABT)
    assert (w["status"], w["lines"]) == ("note_unlisted", [])


@pytest.mark.criterion("C4")
def test_note_list_is_locked_and_defined_by_the_taxonomy(kit):
    # oracle: the SHA-256 pinned in the spec, and the us-gaap 2025 documentation of every
    # listed element (committed excerpt)
    assert kit.sha(kit.notes_path) == NOTES_SHA256
    live = kit.live / "note_concepts.yaml"
    assert live.exists(), "configs/eval/v2/note_concepts.yaml is missing"
    # the live list may only shrink (requirement 4); docs/specs/key-definitions.md
    # requirement 7 removes Debt and narrows DeferredRevenue to its totals
    live_notes = load_note_concepts(live)
    notes = load_note_concepts(kit.notes_path)
    for family, concepts in live_notes.items():
        assert set(concepts) <= set(notes.get(family, [])), family
    assert notes["EffectiveTaxRate"] == [ETR]
    assert notes["InterestIncome"] == [III, "us-gaap:InvestmentIncomeInterestAndDividend"]
    assert sum(len(v) for v in notes.values()) == 19
    for family, concepts in notes.items():
        for c in concepts:
            doc = kit.taxonomy["elements"][c]
            assert doc["documentation"] and doc["label"], (family, c)
    # a broader total or a different measure is never a note concept
    for broader in ("us-gaap:NonoperatingIncomeExpense",
                    "us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent",
                    "us-gaap:CostsAndExpenses", "us-gaap:CommonStockSharesIssued",
                    "us-gaap:InvestmentIncomeNet"):
        assert all(broader not in v for v in notes.values()), broader


BAD = [
    "version: 2\nfamilies: {}\n",
    "version: 1\nfamilies:\n  X: [NoTaxonomy]\n",
    "version: 1\nfamilies:\n  X: ['us-gaap:A', 'us-gaap:A']\n",
    "version: 1\nfamilies: {}\nextra: 1\n",
]


@pytest.mark.criterion("C4")
@pytest.mark.parametrize("text", BAD)
def test_malformed_note_lists_are_refused(tmp_path: Path, text):
    # oracle: requirement 4, load_note_concepts' ValueError cases
    p = tmp_path / "notes.yaml"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_note_concepts(p)


@pytest.mark.criterion("C4")
@given(drop=st.integers(min_value=0, max_value=4))
def test_shrinking_the_list_unlists_exactly_the_dropped_concept(kit, drop):
    # oracle: requirement 4 (a note key is accepted only on a listed concept); the
    # hand-checked note keys of _note_keys, one concept removed from a copy of the list
    e = kit.env()
    keys = _note_keys(kit)
    family, k, _adsh = keys[drop]
    notes = {f: [c for c in cs if not (f == family and c == k["concept"])]
             for f, cs in e.notes.items()}
    for fam, kk, adsh in keys:
        w = statement_witness(e.tables, e.rules, notes, fam, kk, adsh)
        removed = fam == family and kk["concept"] == k["concept"]
        assert w["status"] == ("note_unlisted" if removed else "note_witnessed")
