"""C1: the locked judging oracle loads, and depends on nothing in the rules under test.

Oracle: the retired key-statement-witness r1 copy (each family's statements and
parenthetical flag) and requirement 2 of docs/specs/witness-rules-r2.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import load_oracle

NOT_JUDGED = {"Debt", "DeferredRevenue", "EffectiveTaxRate"}
UNITS = {"EarningsPerShareBasic": "USD/shares", "EarningsPerShareDiluted": "USD/shares",
         "DividendsPerShare": "USD/shares", "WeightedAverageSharesBasic": "shares",
         "WeightedAverageSharesDiluted": "shares", "SharesOutstanding": "shares"}


@pytest.mark.criterion("C1")
def test_locked_oracle_follows_r1(kit):
    # oracle: requirement 2 — 51 r1 families minus the three not judged; statements and
    # parenthetical copied from r1; instant exactly when r1's first statement is BS
    r1 = load_statement_rules(kit.r1_rules)
    assert kit.sha(kit.r1_rules) == kit.r1_sha
    oracle = load_oracle(kit.oracle)
    assert len(oracle) == 48
    assert set(oracle) == set(r1) - NOT_JUDGED
    for fam, o in oracle.items():
        assert o["statements"] == r1[fam]["statements"], fam
        assert o["parenthetical"] is r1[fam]["parenthetical"], fam
        assert o["period"] == ("instant" if r1[fam]["statements"][0] == "BS" else "duration"), fam
        assert o["unit"] == UNITS.get(fam, "USD"), fam
        assert o["elements"] and all(":" not in e for e in o["elements"]), fam


@pytest.mark.criterion("C1")
def test_locked_oracle_spot_elements(kit):
    # oracle: us-gaap element names whose definition is the measure (spec requirement 2):
    # Assets is total assets; NetIncomeLoss is net income attributable to the parent
    oracle = load_oracle(kit.oracle)
    assert oracle["TotalAssets"] == {"elements": ["Assets"], "statements": ["BS"],
                                     "parenthetical": False, "period": "instant", "unit": "USD"}
    assert oracle["NetIncome"]["elements"] == ["NetIncomeLoss"]
    assert oracle["CashAndEquivalents"]["elements"] == ["CashAndCashEquivalentsAtCarryingValue"]
    assert oracle["SharesOutstanding"]["statements"] == ["BS", "EQ"]
    assert oracle["SharesOutstanding"]["parenthetical"] is True


GOOD = ("version: 1\nfamilies:\n  X: {elements: [Assets], statements: [BS], "
        "parenthetical: false, period: instant, unit: USD}\n")
BAD = [
    GOOD.replace("version: 1", "version: 2"),
    GOOD.replace("unit: USD}", "unit: USD, colour: red}"),
    GOOD.replace("elements: [Assets], ", ""),
    GOOD.replace("[Assets]", "[]"),
    GOOD.replace("[Assets]", "[us-gaap:Assets]"),
    GOOD.replace("[BS]", "[XX]"),
    GOOD.replace("period: instant", "period: annual"),
    GOOD.replace("parenthetical: false", "parenthetical: maybe"),
    GOOD.replace("period: instant, ", ""),
]


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("text", BAD)
def test_malformed_oracle_is_refused(tmp_path: Path, text):
    # oracle: requirement 2, the loader's ValueError cases
    bad = tmp_path / "bad.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_oracle(bad)
    ok = tmp_path / "ok.yaml"
    ok.write_text(GOOD, encoding="utf-8")
    assert load_oracle(ok) == {"X": {"elements": ["Assets"], "statements": ["BS"],
                                     "parenthetical": False, "period": "instant", "unit": "USD"}}


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
def test_oracle_is_independent_of_layout(kit, tmp_path: Path):
    # oracle: the unperturbed load of the same oracle — reversing the family order and
    # writing each family in flow style changes no loaded value
    import yaml

    doc = yaml.safe_load(kit.oracle.read_text(encoding="utf-8"))
    fams = list(doc["families"].items())[::-1]
    lines = ["version: 1", "families:"]
    for fam, o in fams:
        lines.append(f"  {fam}: {{unit: {o['unit']}, period: {o['period']}, "
                     f"parenthetical: {str(o['parenthetical']).lower()}, "
                     f"statements: [{', '.join(o['statements'])}], "
                     f"elements: [{', '.join(o['elements'])}]}}")
    p = tmp_path / "reordered.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    base = load_oracle(kit.oracle)
    assert len(base) == 48
    assert load_oracle(p) == base
