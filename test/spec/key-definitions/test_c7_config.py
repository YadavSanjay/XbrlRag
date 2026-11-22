"""C7: the definitions are named, and the live config carries them (requirement 7).

Oracle: requirements 1 and 7 of docs/specs/key-definitions.md; the locked Debt and
DeferredRevenue rules in fixtures/statement_lines_kd.yaml; the retired key-statement-witness
locked note list and resolutions.
"""

from __future__ import annotations

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

DEFINED = {"Debt": "debt_total", "DeferredRevenue": "deferred_revenue_total",
           "NetIncome": "net_income_parent", "ComprehensiveIncome": "comprehensive_income_parent"}


@pytest.mark.criterion("C7")
def test_definition_ids(kit):
    # oracle: requirement 1 — the four definition ids
    from src.eval.keys.key_definitions import key_definition

    assert {f: key_definition(f) for f in DEFINED} == DEFINED


@pytest.mark.criterion("C7")
@given(family=st.sampled_from(["Revenue", "TotalAssets", "NetIncomeToCommon",
                               "StockholdersEquity", "DebtIssued", "DebtRepaid", "Capex"]))
def test_other_families_have_no_definition(family):
    # oracle: requirement 1 — only the four families carry a definition id
    from src.eval.keys.key_definitions import key_definition

    assert key_definition(family) is None
    assert key_definition("Debt") == "debt_total"


@pytest.mark.criterion("C7")
def test_live_rules_carry_the_locked_debt_and_deferred_revenue_rules(kit):
    # oracle: requirement 7 — the live Debt and DeferredRevenue entries load to the locked
    # copies; the live file still loads with every family. Debt may instead be
    # debt-ifrs-elements' locked entry (this rule plus the IFRS borrowing elements)
    from src.eval.keys.statement_witness import load_statement_rules

    locked = load_statement_rules(kit.rules_path)
    live = load_statement_rules(kit.live / "statement_lines.yaml")
    debt = load_statement_rules(kit.repo / "test" / "spec" / "debt-ifrs-elements" / "fixtures"
                                / "statement_lines_debt.yaml")
    assert live["Debt"] in (locked["Debt"], debt["Debt"])
    assert {k: v for k, v in debt["Debt"].items() if k != "elements"} == locked["Debt"]
    assert live["DeferredRevenue"] == locked["DeferredRevenue"]
    assert locked["Debt"]["combine"] == "sum"
    assert locked["DeferredRevenue"]["combine"] == "sum"
    assert len(live) == 51


@pytest.mark.criterion("C7")
def test_live_note_list_shrinks_as_stated(kit):
    # oracle: requirement 7 — Debt removed; DeferredRevenue keeps only the two totals; every
    # other family's list is the retired key-statement-witness locked list's
    from src.eval.keys.statement_witness import load_note_concepts

    locked = load_note_concepts(kit.ksw / "note_concepts.yaml")
    live = load_note_concepts(kit.live / "note_concepts.yaml")
    assert "Debt" not in live
    assert live["DeferredRevenue"] == ["us-gaap:ContractWithCustomerLiability",
                                       "us-gaap:DeferredRevenue"]
    others = {f: v for f, v in locked.items() if f not in ("Debt", "DeferredRevenue")}
    assert {f: v for f, v in live.items() if f != "DeferredRevenue"} == others


@pytest.mark.criterion("C7")
def test_live_resolutions_drop_the_orcl_deferred_revenue_entry(kit):
    # oracle: requirement 7 — ORCL FY2024 deferred revenue (10,546) is keyed by rule, so its
    # resolution is removed; the live list stays a subset of the locked one
    from src.eval.keys.statement_witness import check_resolutions, load_resolutions

    live = load_resolutions(kit.live / "key_resolutions.yaml")
    assert not [r for r in live if r["family"] == "DeferredRevenue"
                and r["cik"] == "0001341439" and r["fiscal_year"] == 2024]
    assert check_resolutions(live, load_resolutions(kit.ksw / "key_resolutions.yaml")) == len(live)


@pytest.mark.criterion("C7")
def test_lexicon_names_total_debt(kit):
    # oracle: requirement 7 — Debt is phrased as total debt; no Debt surface form or
    # paraphrase form names long-term debt; DeferredRevenue gains "total deferred revenue"
    lex = yaml.safe_load((kit.live / "lexicon.yaml").read_text(encoding="utf-8"))["metrics"]
    debt = lex["Debt"]
    assert debt["label"] == "Total Debt"
    assert "total debt" in debt["synonyms"]
    surfaces = [debt["label"], *debt.get("synonyms", []), *debt.get("shorthand", [])]
    assert not [s for s in surfaces if "long-term" in s.lower() or s.upper().startswith("LT ")]
    assert "total deferred revenue" in lex["DeferredRevenue"]["synonyms"]
    forms = yaml.safe_load((kit.live / "paraphrase_forms.yaml").read_text(encoding="utf-8"))
    debt_forms = forms["metrics"].get("Debt", [])
    assert not [f for f in debt_forms if "long-term" in f.lower() or "lt " in f.lower()]
