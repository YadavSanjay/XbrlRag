"""C3: the standard-label invariant reads a stripping rule's patterns on the label's head.

Oracle: requirement 3 of docs/specs/capex-strip-qualifiers.md (statement-rules-by-element
review point 9): for a rule with `strip_qualifiers`, witness-rules-r2-1 requirement 1.1 is
evaluated on the head of the standard label, as the engine applies the rule. Heads below are
constructed (a cue-free head plus one qualifier), so the expected head is known by hand.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

LABEL = "Intangible Assets, Net (Excluding Goodwill)"      # us-gaap 2025 standard label
HEAD = "Intangible Assets, Net"                             # by hand (requirement 3 of SRBE)


def _rule(exclude=(), require=(), strip=True) -> dict:
    rule = {"statements": ["BS"], "parenthetical": False, "include": ["intangible"],
            "require": list(require), "exclude": list(exclude), "section": None,
            "prefer": [], "combine": "one", "substitutes": {}}
    if strip:
        rule["strip_qualifiers"] = True
    return rule


def _oracle(elements) -> dict:
    return {"IntangibleAssets": {"elements": list(elements), "statements": ["BS"],
                                 "parenthetical": False, "period": "instant", "unit": "USD"}}


@pytest.mark.criterion("C3")
def test_qualifier_terms_do_not_violate_a_stripping_rule(kit):
    # oracle: requirement 3 by hand - exclude "goodwill" rejects LABEL in full but not its
    # head, so a stripping rule has no violation and a non-stripping one has one; require
    # "goodwill" holds on LABEL but not on the head, so only the stripping rule violates it
    from src.eval.keys.witness_measure import standard_label_violations

    labels = {"IntangibleAssetsNetExcludingGoodwill": LABEL}
    oracle = _oracle(["IntangibleAssetsNetExcludingGoodwill"])
    v = standard_label_violations
    assert v({"IntangibleAssets": _rule(exclude=["goodwill"])}, oracle, labels) == []
    assert v({"IntangibleAssets": _rule(exclude=["goodwill"], strip=False)}, oracle,
             labels) == [{"family": "IntangibleAssets",
                          "element": "IntangibleAssetsNetExcludingGoodwill", "label": LABEL,
                          "field": "exclude", "pattern": "goodwill"}]
    assert v({"IntangibleAssets": _rule(require=["goodwill"])}, oracle, labels) == [
        {"family": "IntangibleAssets", "element": "IntangibleAssetsNetExcludingGoodwill",
         "label": LABEL, "field": "require", "pattern": "goodwill"}]
    assert v({"IntangibleAssets": _rule(require=["goodwill"], strip=False)}, oracle,
             labels) == []
    # a term in the head still violates (the head is "Intangible Assets, Net")
    assert [x["pattern"] for x in v({"IntangibleAssets": _rule(exclude=[r"\bnet\b"])}, oracle,
                                    labels)] == [r"\bnet\b"]


HEADS = ["Payments to Acquire Property, Plant, and Equipment", "Intangible Assets, Net",
         "Research and Development Expense", "Proceeds from Issuance of Debt"]
TERMS = ["goodwill", "issuance costs", "acquired", "property", "debt", "net", "expense"]


@pytest.mark.criterion("C3")
@given(head=st.sampled_from(HEADS), cue=st.sampled_from(["Excluding", "Net of", "Other than"]),
       tail=st.sampled_from(["Goodwill", "Issuance Costs", "Acquired in Process Cost"]),
       form=st.sampled_from(["{h} ({c} {t})", "{h}, {c} {t}"]),
       exclude=st.lists(st.sampled_from(TERMS), max_size=3, unique=True),
       require=st.lists(st.sampled_from(TERMS), max_size=2, unique=True))
def test_stripping_rule_violations_are_those_of_the_head(kit, head, cue, tail, form, exclude,
                                                        require):
    # oracle: requirement 3 (property), written out - for a stripping rule the violations
    # are the exclude patterns matching the head and the require patterns missing from it,
    # sorted by (field, pattern), each reporting the full standard label
    from src.eval.keys.witness_measure import standard_label_violations

    label = form.format(h=head, c=cue, t=tail)
    want = sorted([("exclude", p) for p in exclude if re.search(p, head, re.IGNORECASE)]
                  + [("require", p) for p in require if not re.search(p, head, re.IGNORECASE)])
    got = standard_label_violations({"IntangibleAssets": _rule(exclude, require)},
                                    _oracle(["E"]), {"E": label})
    assert [(x["field"], x["pattern"]) for x in got] == want
    assert all(x["label"] == label for x in got)


@pytest.mark.criterion("C3")
def test_live_rules_keep_both_invariants(kit):
    # oracle: requirement 3 and witness-rules-r2-1 requirements 1.1-1.2 - the live rules
    # (Capex now stripping) reject no oracle element's standard label and have no unbounded
    # exclude or require pattern
    from src.eval.keys.witness_measure import (
        boundary_violations,
        load_oracle,
        standard_label_violations,
    )

    live = kit.loaded().live
    assert live["Capex"].get("strip_qualifiers") is True
    assert standard_label_violations(live, load_oracle(kit.oracle), kit.labels()) == []
    assert boundary_violations({f: r for f, r in live.items()
                                if f not in kit.unjudged}) == []
