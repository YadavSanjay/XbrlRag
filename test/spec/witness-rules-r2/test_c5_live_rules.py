"""C5: the live r2 rules load, cover the r1 families and name no filer.

Oracle: requirement 1 of docs/specs/witness-rules-r2.md. The key-statement-witness
hand-checked lines are run against the live file in that spec's C2 file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import rule_identifier_violations

CLEAN = """# Comment may cite ASC 2016-02 and CIK 0000320193 freely.
version: 1
families:
  X:
    statements: [IS]
    include: ['^total revenues?', 'net sales']
    exclude: ['cost of']
    substitutes:
      us-gaap:CostsAndExpenses: {code: includes_cost_of_revenue, note: Total costs.}
      srt:Foo: {code: c, note: n}
"""


@pytest.mark.criterion("C5")
def test_live_rules_are_r2_and_clean(kit):
    # oracle: requirement 1 — r2 replaces the content, keeps the 51 r1 families, loads, and
    # holds no filer identifier
    assert kit.live_rules.exists()
    assert kit.sha(kit.live_rules) != kit.r1_sha
    live = load_statement_rules(kit.live_rules)
    r1 = load_statement_rules(kit.r1_rules)
    assert set(live) == set(r1)
    assert len(live) == 51
    assert rule_identifier_violations(kit.live_rules) == []


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(("old", "new"), [
    ("'net sales'", "'net sales', '0000320193'"),            # a CIK
    ("'net sales'", "'net sales', '0000320193-25-000079'"),  # an accession
    ("'net sales'", "'net sales 2023'"),                     # a year
    ("'cost of'", "'cost of', '14813'"),                     # an amount
    ("srt:Foo", "ext:Foo"),                                  # an extension substitute
    ("srt:Foo", "abc:Foo"),                                  # an unknown taxonomy
])
def test_identifiers_are_flagged(tmp_path: Path, old, new):
    # oracle: requirement 1 — outside comments no run of four or more digits; substitutes
    # only in us-gaap, ifrs-full, srt or dei
    p = tmp_path / "rules.yaml"
    p.write_text(CLEAN.replace(old, new), encoding="utf-8")
    load_statement_rules(p)
    got = rule_identifier_violations(p)
    assert len(got) >= 1
    assert all(isinstance(m, str) and m for m in got)


@pytest.mark.criterion("C5")
def test_clean_rules_have_no_violation(tmp_path: Path):
    # oracle: requirement 1 — digits inside a # comment are allowed; srt and us-gaap
    # substitutes are allowed; three-digit runs are allowed
    p = tmp_path / "rules.yaml"
    p.write_text(CLEAN.replace("'net sales'", "'net sales', 'form 10-k', 'top 100'"),
                 encoding="utf-8")
    assert rule_identifier_violations(p) == []


@pytest.mark.criterion("C5")
@given(digits=st.text(alphabet="0123456789", min_size=1, max_size=12))
def test_digit_runs_flag_from_four(tmp_path_factory, digits):
    # oracle: requirement 1 — a run of four or more digits in a pattern is an identifier
    p = tmp_path_factory.mktemp("r") / "rules.yaml"
    p.write_text(CLEAN.replace("'net sales'", f"'net sales {digits}'"), encoding="utf-8")
    got = rule_identifier_violations(p)
    assert isinstance(got, list)
    assert (len(got) >= 1) is (len(digits) >= 4)
