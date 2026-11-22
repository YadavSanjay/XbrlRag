"""C5: without the new fields, the label test is exactly key-statement-witness's.

Oracle: key-statement-witness requirement 2 step 1 (a candidate's label matches any include,
all require and no exclude pattern, case-insensitive ``re.search``), computed in the test;
requirement 5 of docs/specs/statement-rules-by-element.md (no rule content changes here).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import line_is_candidate, statement_lines


def _legacy(rule: dict, label: str) -> bool:
    def hit(p: str) -> bool:
        return re.search(p, label, re.IGNORECASE) is not None
    return (any(hit(p) for p in rule["include"]) and all(hit(p) for p in rule["require"])
            and not any(hit(p) for p in rule["exclude"]))


def _without_elements(rule: dict) -> dict:
    return {k: v for k, v in rule.items() if k not in ("elements", "strip_qualifiers")}


def _debt_ifrs_elements() -> list[str]:
    """The Debt `elements` of debt-ifrs-elements' locked copy (its spec PR edits this test)."""
    copy = (Path(__file__).resolve().parents[1] / "debt-ifrs-elements" / "fixtures"
            / "statement_lines_debt.yaml")
    return yaml.safe_load(copy.read_text(encoding="utf-8"))["families"]["Debt"]["elements"]


@pytest.mark.criterion("C5")
def test_live_rules_on_every_fixture_line_match_the_legacy_test(kit):
    # oracle: key-statement-witness requirement 2 step 1, computed above, for every live
    # family and every fixture statement line (TSM balance sheet, TSLA cash flows); and
    # requirement 5: no live family uses `elements` or `strip_qualifiers`, except Debt's
    # `elements`, which may be exactly debt-ifrs-elements' locked list, and Capex's
    # `strip_qualifiers: true` (capex-strip-qualifiers); the legacy test is then compared on
    # the rules without the new fields
    raw = yaml.safe_load(kit.live_rules.read_text(encoding="utf-8"))["families"]
    assert not [f for f, r in raw.items() if "strip_qualifiers" in r and f != "Capex"]
    assert raw["Capex"].get("strip_qualifiers", True) is True
    assert not [f for f, r in raw.items() if "elements" in r and f != "Debt"]
    if "elements" in raw["Debt"]:
        assert kit.env().rules["Debt"]["elements"] == _debt_ifrs_elements()
    e = kit.env()
    lines = statement_lines(e.tables, kit.TSM) + statement_lines(e.tables, kit.TSLA)
    assert len(lines) == 55 + 37
    agree = 0
    for family, rule in e.rules.items():
        rule = _without_elements(rule)
        for ln in lines:
            concept = f"x:{ln['tag']}"
            assert line_is_candidate(rule, ln["plabel"], concept) is _legacy(rule, ln["plabel"]), (
                family, ln["plabel"])
            agree += 1
    assert agree == 51 * 92


LABEL_WORDS = ["total", "debt", "excluding", "finance", "leases", "net", "of", "revenue",
               "cost", "of", "sales", "purchases", "property", "and", "equipment", "other",
               "than", "income", "taxes", "(", ")", ",", "deferred", "cash", "operating",
               "activities", "basic", "diluted", "per", "share", "goodwill", "intangible"]


@pytest.mark.criterion("C5")
@given(pick=st.data(), words=st.lists(st.sampled_from(LABEL_WORDS), min_size=1, max_size=9),
       concept=st.sampled_from(["us-gaap:LongTermDebtNoncurrent", "ifrs-full:LongtermBorrowings",
                                "ext:Anything"]))
def test_a_rule_without_new_fields_ignores_qualifiers_and_elements(kit, pick, words, concept):
    # oracle: the legacy test above (property) - for any live family, any label (cue words
    # included) and any concept, a rule without the new fields decides as before: qualifiers
    # are not stripped and the concept plays no part
    rules = {f: _without_elements(r) for f, r in kit.env().rules.items()}
    family = pick.draw(st.sampled_from(sorted(rules)))
    label = " ".join(words)
    assert line_is_candidate(rules[family], label, concept) is _legacy(rules[family], label)
    # a control the legacy test always accepts: the rule's first include pattern's own hit
    assert line_is_candidate(rules["TotalAssets"], "Total assets " + label, concept) is \
        _legacy(rules["TotalAssets"], "Total assets " + label) is True
