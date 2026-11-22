"""C1: the live Debt rule lists the ifrs-full borrowing elements, chosen by their definitions.

Oracle: requirements 1-2 of docs/specs/debt-ifrs-elements.md, and the IFRS Accounting
Standards Taxonomy 2024-03-27 (fixtures/taxonomy_excerpt_ifrs.json: standard label,
documentation, period type, balance and type of every listed and every left-out element, from
the official label, documentation and core-schema files pinned by SHA-256 in the spec Notes).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

REPO = Path(__file__).resolve().parents[3]
LISTED = sorted("ifrs-full:" + n for n in json.loads(
    (Path(__file__).parent / "fixtures" / "taxonomy_excerpt_ifrs.json").read_text(
        encoding="utf-8"))["listed"])
# The pinned sources of the excerpt (spec Notes, "Taxonomy excerpt").
SOURCES = {
    "lab_full_ifrs-en_2024-03-27.xml":
        "c43bb860e64d7a9fbb9995c34c7926be80b213dedcef17c11620c006dbe05ae1",
    "doc_full_ifrs-en_2024-03-27.xml":
        "a825ded2c3daccd523e54d0272c6f42da13da449668127dbf235b82bd5ba3d6b",
    "full_ifrs-cor_2024-03-27.xsd":
        "50f853021fd4268e4944ef01bc21aad84cb5f8e69d85fcb2b0ceec3951fe384c",
}
# A definition names a borrowing when it states an amount of one of these (requirement 2).
BORROWING = re.compile(r"borrowings|loans received|loans received from banks|bonds issued|"
                       r"bonds\b|notes and debentures|commercial paper|indebtedness|"
                       r"debt instruments issued|funds that the entity is obligated to repay",
                       re.IGNORECASE)
# The reasons an element is left out, each with the words its definition must contain.
REASONS = {
    "lease": r"lease",
    "cash_component": r"cash balances|cash and cash equivalents",
    "net_of_assets": r"net debt",
    "measurement_category": r"amortised cost|fair value through profit or loss",
    "undiscounted_cash_flows": r"undiscounted cash flows",
    "business_combination": r"acquisition date|business combination",
    "not_drawn": r"undrawn|commitments",
    "subset_disclosure": r"in default|in breach",
}


def _live_debt(kit) -> dict:
    return kit.env().live["Debt"]


@pytest.mark.criterion("C1")
def test_live_debt_rule_lists_exactly_the_borrowing_elements(kit):
    # oracle: requirement 1 - the live Debt rule carries `elements`, exactly the 29 listed
    # ifrs-full elements of the excerpt in its order; every other Debt field is
    # key-definitions' locked rule, and DeferredRevenue is unchanged
    live = _live_debt(kit)
    assert live.get("elements") == kit.listed
    assert len(kit.listed) == 29
    kd = kit.env().kd
    assert {k: v for k, v in live.items() if k != "elements"} == kd["Debt"]
    assert kit.env().live["DeferredRevenue"] == kd["DeferredRevenue"]
    assert "strip_qualifiers" not in live


@pytest.mark.criterion("C1")
def test_live_debt_rule_loads_equal_to_the_locked_copy(kit):
    # oracle: requirement 1 - the locked copy fixtures/statement_lines_debt.yaml is the spec's
    # rule; the live Debt and DeferredRevenue entries load equal to it
    locked = kit.env().locked
    assert locked["Debt"]["elements"] == kit.listed
    assert _live_debt(kit) == locked["Debt"]
    assert kit.env().live["DeferredRevenue"] == locked["DeferredRevenue"]


@pytest.mark.criterion("C1")
def test_every_listed_element_is_a_borrowing_balance_by_definition(kit):
    # oracle: the IFRS taxonomy 2024 (excerpt) - each listed element is a non-abstract
    # instant credit monetary item present in the 2022-2025 taxonomies whose documentation
    # states an amount of borrowings, loans, bonds, notes and debentures, commercial paper or
    # debt instruments issued; the excerpt's sources are the pinned files
    assert {n: s["sha256"] for n, s in kit.excerpt["sources"].items()} == SOURCES
    for name, e in kit.excerpt["listed"].items():
        assert (e["period_type"], e["balance"], e["type"], e["abstract"]) == (
            "instant", "credit", "xbrli:monetaryItemType", False), name
        assert e["taxonomies"] == ["2022", "2023", "2024", "2025"], name
        assert BORROWING.search(e["documentation"]), name
        assert not re.search(r"lease|undiscounted|undrawn|net debt", e["documentation"],
                             re.IGNORECASE), name
    assert _live_debt(kit).get("elements") == kit.listed


@pytest.mark.criterion("C1")
def test_every_left_out_element_is_out_for_its_stated_reason(kit):
    # oracle: requirement 2 - each element the spec considered and left out carries one of
    # the stated reasons, its documentation shows that reason, and it is not listed
    listed = _live_debt(kit).get("elements") or []
    assert listed == kit.listed
    assert len(kit.excerpt["excluded"]) == 14
    for name, e in kit.excerpt["excluded"].items():
        assert e["reason"] in REASONS, name
        assert re.search(REASONS[e["reason"]], e["documentation"], re.IGNORECASE), name
        assert "ifrs-full:" + name not in listed


@pytest.mark.criterion("C1")
@given(element=st.sampled_from(LISTED))
def test_no_listed_element_is_a_stated_substitute(element):
    # oracle: requirement 1 and key-currency-and-definition requirement 3 (property) - a
    # listed element is a substitute neither in the rule nor in the committed concept
    # mapping, so merging the mapping into the live rules never raises
    from src.eval.keys.statement_witness import (
        load_mapping_substitutes,
        load_statement_rules,
        merge_mapping_substitutes,
    )

    live = load_statement_rules(REPO / "configs" / "eval" / "v2" / "statement_lines.yaml")
    mapping_path = REPO / "configs" / "concept_mapping" / "concept_family_coverage.yaml"
    assert element in (live["Debt"].get("elements") or [])
    mapping = load_mapping_substitutes(mapping_path)
    assert element not in live["Debt"]["substitutes"]
    assert element not in mapping.get("Debt", {})
    merged = merge_mapping_substitutes(live, mapping)
    assert merged["Debt"]["elements"] == live["Debt"]["elements"]
