"""C1: the rules schema gains `elements` and `strip_qualifiers`, validated, sparse when unused.

Oracle: requirement 1 of docs/specs/statement-rules-by-element.md (field forms, refusals,
the loaded shape) and key-statement-witness requirement 2 (the shape of a rule that uses
neither field, unchanged).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules

LEGACY = {"statements": ["BS"], "parenthetical": False, "include": ["^total assets"],
          "require": [], "exclude": [], "section": None, "prefer": [], "combine": "one",
          "substitutes": {}}

GOOD = """\
  TotalAssets: {statements: [BS], include: ['^total assets']}
  Debt:
    statements: [BS]
    include: ['\\bdebt\\b']
    elements: [ifrs-full:CurrentPortionOfLongtermBorrowings, us-gaap:LongTermDebtNoncurrent]
    combine: sum
  Capex: {statements: [CF], include: ['purchases'], strip_qualifiers: true}
  Plain: {statements: [IS], include: ['a'], strip_qualifiers: false}
"""

# Each refused for its own reason (requirement 1).
BAD = [
    # elements not a list
    "  X: {statements: [BS], include: ['a'], elements: us-gaap:LongTermDebtNoncurrent}\n",
    # an empty list
    "  X: {statements: [BS], include: ['a'], elements: []}\n",
    # not <taxonomy>:<LocalName>
    "  X: {statements: [BS], include: ['a'], elements: [LongTermDebtNoncurrent]}\n",
    # a filer extension
    "  X: {statements: [BS], include: ['a'], elements: ['ext:TermDebt']}\n",
    # a non-standard taxonomy prefix
    "  X: {statements: [BS], include: ['a'], elements: ['aapl:TermDebt']}\n",
    # a duplicate
    ("  X: {statements: [BS], include: ['a'], elements: [us-gaap:DebtCurrent, "
     "us-gaap:DebtCurrent]}\n"),
    # a non-string element
    "  X: {statements: [BS], include: ['a'], elements: [7]}\n",
    # an element that is also a stated substitute of the family
    ("  X:\n    statements: [IS]\n    include: ['a']\n    elements: [us-gaap:CostsAndExpenses]\n"
     "    substitutes:\n      us-gaap:CostsAndExpenses: {code: c, note: n}\n"),
    # strip_qualifiers not a boolean
    "  X: {statements: [IS], include: ['a'], strip_qualifiers: 'yes'}\n",
    "  X: {statements: [IS], include: ['a'], strip_qualifiers: 1}\n",
]


def _load(path: Path) -> dict:
    try:
        return load_statement_rules(path)
    except ValueError as exc:
        pytest.fail(f"a rules file using the new fields must load: {exc}")


@pytest.mark.criterion("C1")
def test_new_fields_load_sparse(kit, tmp_path):
    # oracle: requirement 1 - a rule using neither field loads as key-statement-witness
    # requirement 2 fixed it (9 keys); `elements` loads as a list in file order, present only
    # when given; `strip_qualifiers` present only when true
    rules = _load(kit.write_rules(tmp_path, GOOD))
    assert rules["TotalAssets"] == LEGACY
    assert rules["Debt"]["elements"] == ["ifrs-full:CurrentPortionOfLongtermBorrowings",
                                         "us-gaap:LongTermDebtNoncurrent"]
    assert "strip_qualifiers" not in rules["Debt"]
    assert set(rules["Debt"]) == set(LEGACY) | {"elements"}
    assert rules["Capex"]["strip_qualifiers"] is True
    assert set(rules["Capex"]) == set(LEGACY) | {"strip_qualifiers"}
    assert set(rules["Plain"]) == set(LEGACY)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("body", BAD)
def test_malformed_new_fields_are_refused(kit, tmp_path, body):
    # oracle: requirement 1, the loader's ValueError cases for the two fields; the good file
    # beside it loads (the refusal is the field's, not the schema's)
    bad = tmp_path / "bad"
    bad.mkdir()
    with pytest.raises(ValueError):
        load_statement_rules(kit.write_rules(bad, body))
    assert _load(kit.write_rules(tmp_path, GOOD))["Capex"]["strip_qualifiers"] is True


ELEMENT_POOL = ["us-gaap:LongTermDebtNoncurrent", "us-gaap:DebtCurrent", "srt:SomeElement",
                "ifrs-full:CurrentPortionOfLongtermBorrowings",
                "dei:EntityCommonStockSharesOutstanding", "ifrs-full:LongtermBorrowings"]


@pytest.mark.criterion("C1")
@given(pick=st.data(), elements=st.lists(st.sampled_from(ELEMENT_POOL), min_size=1, max_size=4,
                                         unique=True))
def test_adding_fields_to_a_live_rule_adds_exactly_them(kit, tmp_path_factory, pick, elements):
    # oracle: requirement 1 (metamorphic) - any live family with `strip_qualifiers: false`
    # added loads equal to the live rule; with `elements` added (none a substitute) it loads
    # as the live rule plus exactly that key
    raw = yaml.safe_load(kit.live_rules.read_text(encoding="utf-8"))["families"]
    live = load_statement_rules(kit.live_rules)
    family = pick.draw(st.sampled_from(sorted(raw)))
    elements = [e for e in elements if e not in live[family]["substitutes"]]
    if not elements:
        elements = ["dei:EntityCommonStockSharesOutstanding"]
    tmp = tmp_path_factory.mktemp("rules")
    doc = {"version": 1, "families": {
        "Off": {**raw[family], "strip_qualifiers": False},
        "On": {**raw[family], "elements": elements, "strip_qualifiers": True}}}
    path = tmp / "r.yaml"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    got = _load(path)
    assert got["Off"] == {k: v for k, v in live[family].items() if k != "strip_qualifiers"}
    assert got["On"] == {**live[family], "elements": elements, "strip_qualifiers": True}
