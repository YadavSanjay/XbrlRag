"""C1: no rule rejects the us-gaap standard label of its own family's measure.

Oracle: requirement 1.1 and 2 of docs/specs/witness-rules-r2-1.md; the us-gaap 2025 standard
labels and definitions in fixtures/taxonomy_excerpt.json (FASB label and documentation
linkbases, SHA-256 recorded there); each listed r2 violation checked by reading the label.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import load_oracle, standard_label_violations

ADDED = {
    "DebtIssued": ["ProceedsFromDebtNetOfIssuanceCosts", "ProceedsFromIssuanceOfMediumTermNotes"],
    "DebtRepaid": ["RepaymentsOfMediumTermNotes"],
}

# r2 rejections of standard labels, each read off the label: the exclude term is in it.
R2_KNOWN = [
    ("CashFromFinancing", "NetCashProvidedByUsedInFinancingActivities",
     "Cash Provided by (Used in) Financing Activity, Including Discontinued Operation",
     "exclude", "discontinued"),
    ("CashFromInvesting", "NetCashProvidedByUsedInInvestingActivities",
     "Cash Provided by (Used in) Investing Activity, Including Discontinued Operation",
     "exclude", "discontinued"),
    ("CashFromOperations", "NetCashProvidedByUsedInOperatingActivities",
     "Cash Provided by (Used in) Operating Activity, Including Discontinued Operation",
     "exclude", "discontinued"),
    ("DebtIssued", "ProceedsFromDebtNetOfIssuanceCosts",
     "Proceeds from Debt, Net of Issuance Costs", "exclude", r"costs?\b"),
    ("IntangibleAssets", "IntangibleAssetsNetExcludingGoodwill",
     "Intangible Assets, Net (Excluding Goodwill)", "exclude", "goodwill"),
    ("ResearchAndDevelopment", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
     "Research and Development Expense (Excluding Acquired in Process Cost)", "exclude",
     "in process"),
]

SYN_RULES = """version: 1
families:
  A:
    statements: [CF]
    include: ['proceeds from']
    exclude: ['costs?\\b', 'short-term']
  B:
    statements: [BS]
    include: ['assets']
    require: ['total']
"""


def _syn(tmp_path: Path):
    p = tmp_path / "rules.yaml"
    p.write_text(SYN_RULES, encoding="utf-8")
    return load_statement_rules(p)


SYN_ORACLE = {
    "A": {"elements": ["P1", "P2", "Gone"], "statements": ["CF"], "parenthetical": False,
          "period": "duration", "unit": "USD"},
    "B": {"elements": ["Q1"], "statements": ["BS"], "parenthetical": False,
          "period": "instant", "unit": "USD"},
}
SYN_LABELS = {"P1": "Proceeds from Debt, Net of Issuance Costs",
              "P2": "Proceeds from Issuance of Long-Term Debt", "Gone": None, "Q1": "Assets"}


def _key(v):
    return (v["family"], v["element"], v["label"], v["field"], v["pattern"])


@pytest.mark.criterion("C1")
def test_synthetic_rules_by_hand(tmp_path):
    # oracle: requirement 1.1 by hand — A's 'costs?\b' matches "Issuance Costs" (P1); P2 is
    # not rejected; Gone (None) is skipped; B's require 'total' does not match "Assets";
    # include is not checked (neither label needs to match it)
    got = standard_label_violations(_syn(tmp_path), SYN_ORACLE, SYN_LABELS)
    assert [_key(v) for v in got] == [
        ("A", "P1", "Proceeds from Debt, Net of Issuance Costs", "exclude", r"costs?\b"),
        ("B", "Q1", "Assets", "require", "total"),
    ]


@pytest.mark.criterion("C1")
def test_missing_label_or_rule_is_refused(tmp_path):
    # oracle: requirement 1.1 — every oracle element needs a label entry (None allowed), and
    # every oracle family a rule; a gap fails loud rather than passing silently
    rules = _syn(tmp_path)
    with pytest.raises(ValueError):
        standard_label_violations(rules, SYN_ORACLE, {k: v for k, v in SYN_LABELS.items()
                                                      if k != "P2"})
    with pytest.raises(ValueError):
        standard_label_violations(rules, {**SYN_ORACLE, "C": SYN_ORACLE["B"]}, SYN_LABELS)
    assert len(standard_label_violations(rules, SYN_ORACLE, SYN_LABELS)) == 2


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(word=st.sampled_from(["Issuance", "Debt", "Long-Term", "Net of", "Securities", "Notes"]))
def test_an_exclude_matching_one_label_adds_exactly_that_violation(tmp_path_factory, word):
    # oracle: requirement 1.1 — adding to A's exclude a literal phrase adds a violation for
    # exactly the A labels containing it (none for a phrase in no label); nothing is removed
    base_dir = tmp_path_factory.mktemp("r")
    rules = _syn(base_dir)
    before = {_key(v) for v in standard_label_violations(rules, SYN_ORACLE, SYN_LABELS)}
    pat = re.escape(word.lower())
    rules["A"] = {**rules["A"], "exclude": [*rules["A"]["exclude"], pat]}
    after = {_key(v) for v in standard_label_violations(rules, SYN_ORACLE, SYN_LABELS)}
    expected_new = {("A", e, SYN_LABELS[e], "exclude", pat) for e in ("P1", "P2")
                    if word.lower() in SYN_LABELS[e].lower()}
    assert after - before == expected_new
    assert before <= after


@pytest.mark.criterion("C1")
def test_oracle_adds_the_three_elements_and_the_excerpt_defines_them(kit):
    # oracle: requirement 2 — r2's oracle plus exactly the three elements; their us-gaap
    # 2025 standard labels and definitions (FASB linkbases; SHA-256 in the excerpt) state a
    # borrowing inflow / outflow of principal
    r2o, new = load_oracle(kit.r2_oracle), load_oracle(kit.oracle)
    assert set(new) == set(r2o) and len(new) == 48
    for f, o in new.items():
        want = [*r2o[f]["elements"], *ADDED.get(f, [])]
        assert o == {**r2o[f], "elements": want}
    ex = kit.excerpt()
    assert ex["source"]["labels_sha256"] == (
        "c10e9ec81f18676a2f047a5fd614baeec9a35b34a755d59d4b67bcd5349f69b0")
    assert ex["source"]["documentation_sha256"] == (
        "27f1d4ba06b0dcb0c91225db2ac746d5adefc167518fa3948abbdfb944dab019")
    el = ex["elements"]
    assert el["us-gaap:ProceedsFromDebtNetOfIssuanceCosts"]["label"] == (
        "Proceeds from Debt, Net of Issuance Costs")
    assert "cash inflow from additional borrowings" in (
        el["us-gaap:ProceedsFromDebtNetOfIssuanceCosts"]["documentation"])
    assert el["us-gaap:ProceedsFromIssuanceOfMediumTermNotes"]["label"] == (
        "Proceeds from Issuance of Medium-Term Note")
    assert el["us-gaap:RepaymentsOfMediumTermNotes"]["label"] == (
        "Repayments of Medium-Term Note")
    names = {e for o in new.values() for e in o["elements"]}
    assert names == set(kit.labels())
    assert sorted(ex["absent"]) == ["us-gaap:EarningsPerShareBasicAndDiluted",
                                    "us-gaap:SalesRevenueNet",
                                    "us-gaap:WeightedAverageNumberOfShareOutstandingBasicAndDiluted"]
    # the stub is exercised so this test is red until the function exists
    assert isinstance(standard_label_violations(
        load_statement_rules(kit.r2_rules), new, kit.labels()), list)


@pytest.mark.criterion("C1")
def test_r2_rejects_these_standard_labels_and_the_live_rules_reject_none(kit):
    # oracle: each R2_KNOWN row read off its label (the exclude term is in the standard
    # label); requirement 1.1 — the live r2.1 rules reject no standard label of the 48
    # judged families. Red reason before the rules change: DebtIssued 'costs?\b' rejects
    # "Proceeds from Debt, Net of Issuance Costs".
    oracle, lab = load_oracle(kit.oracle), kit.labels()
    for _, element, label, _, pattern in R2_KNOWN:
        assert lab[element] == label
        assert re.search(pattern, label, re.IGNORECASE)
    r2 = {_key(v) for v in standard_label_violations(load_statement_rules(kit.r2_rules),
                                                     oracle, lab)}
    assert set(R2_KNOWN) <= r2
    assert standard_label_violations(load_statement_rules(kit.live_rules), oracle, lab) == []
