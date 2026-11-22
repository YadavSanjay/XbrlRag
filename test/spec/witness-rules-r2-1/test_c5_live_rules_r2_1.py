"""C5: the live rules are r2.1: changed from r2, same families, Debt and DeferredRevenue untouched.

Oracle: requirements 1 and 3 of docs/specs/witness-rules-r2-1.md; the accepted r2 rules
copy (SHA-256 equal to r2's judging record) and key-definitions' locked Debt/DeferredRevenue
rules; witness-rules-r2 requirement 1 (identifier check).
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import (
    boundary_violations,
    load_oracle,
    rule_identifier_violations,
    standard_label_violations,
)


@pytest.mark.criterion("C5")
def test_live_rules_are_r2_1(kit):
    # oracle: requirement 3 — r2.1 replaces the accepted r2 content (so its SHA-256 differs
    # from the copy whose SHA-256 is r2's record), keeps the 51 families, names no filer, and
    # leaves Debt and DeferredRevenue as r2 had them or as key-definitions (or, for Debt's
    # IFRS elements, debt-ifrs-elements) locks them
    assert kit.sha(kit.r2_rules) == kit.r2_sha
    assert kit.sha(kit.live_rules) != kit.r2_sha
    live, r2 = load_statement_rules(kit.live_rules), load_statement_rules(kit.r2_rules)
    kd = load_statement_rules(kit.kd_rules)
    debt = load_statement_rules(kit.debt_rules)
    assert set(live) == set(r2) and len(live) == 51
    assert rule_identifier_violations(kit.live_rules) == []
    for f in kit.untouched:
        assert live[f] in (r2[f], kd[f], debt[f])


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(data=st.data())
def test_live_checks_are_not_vacuous(kit, data):
    # oracle: requirements 1.1 and 1.2 — adding to a live judged family an exclude that is one
    # of its standard labels (escaped) adds a label violation for it; adding the unbounded
    # pattern 'zzq' adds exactly one boundary violation
    live = load_statement_rules(kit.live_rules)
    oracle, lab = load_oracle(kit.oracle), kit.labels()
    family = data.draw(st.sampled_from(sorted(oracle)), label="family")
    element = next(e for e in oracle[family]["elements"] if lab[e] is not None)
    pat = r"\b" + re.escape(lab[element])
    changed = {**live, family: {**live[family], "exclude": [*live[family]["exclude"], pat]}}
    got = standard_label_violations(changed, oracle, lab)
    assert {"family": family, "element": element, "label": lab[element], "field": "exclude",
            "pattern": pat} in got
    scope = {f: r for f, r in live.items() if f not in kit.untouched}
    bad = {**scope, family: {**scope[family], "exclude": [*scope[family]["exclude"], "zzq"]}}
    extra = [v for v in boundary_violations(bad) if v["pattern"] == "zzq"]
    assert extra == [{"family": family, "field": "exclude", "pattern": "zzq"}]
