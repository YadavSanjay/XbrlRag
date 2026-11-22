"""C2: score_filing classifies each family of a filing by hand-derivable rules.

Oracle: the constructed rows of fixtures/synthetic (spec Notes, "Synthetic sample"), each
outcome derived by hand from requirement 3.
"""

from __future__ import annotations

import copy

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import load_oracle, score_filing
from src.eval.sec_reference.fsds import load_tables

R1_EXPECTED = {
    "S1": {"TotalAssets": "hit", "Revenue": "hit", "NetIncome": "hit"},
    "S2": {"TotalAssets": "none", "Revenue": "ambiguous", "NetIncome": "wrong"},
    "S3": {"TotalAssets": "no_oracle", "Revenue": "oracle_ambiguous",
           "NetIncome": "no_oracle_selected"},
    "S4": {"TotalAssets": "no_oracle_selected", "Revenue": "no_oracle_selected",
           "NetIncome": "no_oracle"},
}
R2_CHANGES = {"S2": {"TotalAssets": "hit", "NetIncome": "hit"}}
EVALUABLE = {"hit", "none", "ambiguous", "wrong"}


def _env(kit):
    tables = load_tables([kit.synth])
    oracle = load_oracle(kit.synth / "oracle_synthetic.yaml")
    r1 = load_statement_rules(kit.synth / "rules_r1.yaml")
    r2 = load_statement_rules(kit.synth / "rules_r2.yaml")
    return tables, oracle, r1, r2


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", ["S1", "S2", "S3", "S4"])
def test_synthetic_outcomes_r1(kit, name):
    # oracle: spec Notes table, derived by hand from the fixture rows and requirement 3
    tables, oracle, r1, _ = _env(kit)
    assert score_filing(tables, r1, oracle, getattr(kit, name)) == R1_EXPECTED[name]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("name", ["S1", "S2", "S3", "S4"])
def test_synthetic_outcomes_r2(kit, name):
    # oracle: spec Notes table — r2 selects S2's "Assets, total" (2,000) and its
    # "Attributable to parent" (55), and excludes the noncontrolling-interest line
    tables, oracle, _, r2 = _env(kit)
    expected = {**R1_EXPECTED[name], **R2_CHANGES.get(name, {})}
    assert score_filing(tables, r2, oracle, getattr(kit, name)) == expected


@pytest.mark.criterion("C2")
def test_custom_cash_flow_and_prior_period_lines_are_not_oracle_lines(kit):
    # oracle: requirement 3 step 2 — S4's custom-version Assets (900), its Revenues on CF
    # only, and its NetIncomeLoss for 2023 only give no oracle value; removing every S4
    # selection then leaves all three families no_oracle
    tables, oracle, r1, _ = _env(kit)
    never = {f: {**r, "include": ["^never matches$"]} for f, r in r1.items()}
    assert score_filing(tables, never, oracle, kit.S4) == {
        "TotalAssets": "no_oracle", "Revenue": "no_oracle", "NetIncome": "no_oracle"}


def _never(rule):
    return {**rule, "include": ["^never matches$"]}


@pytest.mark.criterion("C2")
@given(blank=st.sets(st.sampled_from(["TotalAssets", "Revenue", "NetIncome"])),
       use_r2=st.booleans())
def test_evaluable_set_does_not_depend_on_the_rules(kit, blank, use_r2):
    # oracle: requirement 3 — evaluability and v* come from the tables and the oracle only:
    # a blanked rule turns every evaluable outcome into none and every no_oracle_selected
    # into no_oracle, and leaves oracle_ambiguous alone
    tables, oracle, r1, r2 = _env(kit)
    rules = copy.deepcopy(r2 if use_r2 else r1)
    for f in blank:
        rules[f] = _never(rules[f])
    for name in ("S1", "S2", "S3", "S4"):
        adsh = getattr(kit, name)
        base = {**R1_EXPECTED[name], **(R2_CHANGES.get(name, {}) if use_r2 else {})}
        got = score_filing(tables, rules, oracle, adsh)
        assert set(got) == set(base)
        for f, out in base.items():
            if f not in blank:
                assert got[f] == out
            elif out in EVALUABLE:
                assert got[f] == "none"
            elif out in ("no_oracle", "no_oracle_selected"):
                assert got[f] == "no_oracle"
            else:
                assert got[f] == out
