"""C2: no exclude or require pattern can start a match inside a word.

Oracle: requirement 1.2 of docs/specs/witness-rules-r2-1.md, applied by hand to constructed
patterns; every hand verdict is confirmed in the test by a witness text ("q" + a matching
string) that the pattern does or does not match at position 1.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import load_statement_rules
from src.eval.keys.witness_measure import boundary_violations, left_bounded

# (pattern, left-bounded?) by hand: a branch is bounded when ^, \b, \A, $ or \Z comes before
# its first consuming item, or that item cannot match a letter; lookarounds are passed over;
# an optional item must be bounded both ways; a branch that ends before any consuming item or
# anchor matches empty, so inside any word, and is unbounded.
TABLE = [
    ("repayment", False),
    (r"\brepayment", True),
    ("^repayment", True),
    (r"costs?\b", False),
    (r"\bcosts?\b", True),
    (r"\(proceeds", True),
    ("%", True),
    (" cost", True),
    ("(three months|90 days) or less", False),
    (r"(\bthree months|90 days) or less", True),
    ("90 days", True),
    ("x?", False),
    (".*cost", False),
    (r"\s*cost", False),
    ("[0-9]+", True),
    ("[a-z]", False),
    ("[^0-9]", False),
    (r"\d", True),
    (r"(?:^|\b)x", True),
    ("(a|^b)", False),
    (r"\bleases?\b", True),
    ("non-?cash", False),
    (r"\bnon-?cash", True),
    ("(?:pre)?payment", False),
    (r"(?<!excluding )\bgoodwill", True),
    ("(?<!excluding )goodwill", False),
]


@pytest.mark.criterion("C2")
@pytest.mark.parametrize(("pattern", "bounded"), TABLE)
def test_left_bounded_table(pattern, bounded):
    # oracle: the hand verdict of TABLE; and by the definition, a leading \b bounds any
    # pattern (after a letter it needs a non-word next) and a leading letter never does
    assert left_bounded(pattern) is bounded
    assert left_bounded(r"\b" + pattern) is True
    assert left_bounded("x" + pattern) is False


@pytest.mark.criterion("C2")
def test_invalid_pattern_is_refused():
    # oracle: requirement 1.2 — a pattern that does not compile is an error, not a verdict
    with pytest.raises(ValueError):
        left_bounded("(unclosed")
    assert left_bounded("^ok") is True


_PREFIXES = [("", False), (r"\b", True), ("^", True), (" ", True), (r"\(", True),
             ("(?:pre)?", False), (r"(?:\b|^)", True), ("[a-z]*", False), ("-?", False),
             ("(?:non-)?", False), (r"(?:\bnon-)?\b", True)]


@pytest.mark.criterion("C2")
@given(prefix=st.sampled_from(_PREFIXES), word=st.from_regex(r"[a-z]{2,8}", fullmatch=True))
def test_prefix_word_patterns(prefix, word):
    # oracle: hand verdict per prefix (_PREFIXES), confirmed by the witness text "q" + word:
    # an unbounded pattern matches it at position 1 (inside the word), a bounded one does not
    pre, bounded = prefix
    pattern = pre + word
    inside = re.compile(pattern, re.IGNORECASE).match("q" + word, 1) is not None
    assert inside is (not bounded)
    assert left_bounded(pattern) is bounded


_BOUNDED = [p for p, b in TABLE if b and not p.startswith("(?<")]


@pytest.mark.criterion("C2")
@given(data=st.data())
def test_bounded_patterns_never_match_after_a_letter(data):
    # oracle: the definition of requirement 1.2 — for a pattern judged bounded, no string it
    # matches, led by a letter and put after the letter "q", is matched at position 1
    pattern = data.draw(st.sampled_from(_BOUNDED), label="pattern")
    rx = re.compile(pattern, re.IGNORECASE)
    s = data.draw(st.from_regex(rx, fullmatch=True), label="match")
    assert left_bounded(pattern) is True
    if s[:1].isascii() and s[:1].isalpha():
        assert rx.match("q" + s, 1) is None


SYN = """version: 1
families:
  X:
    statements: [CF]
    include: ['net sales', 'revenue']
    exclude: ['repayment', '\\bcost', '^sales']
    require: ['total']
  Y:
    statements: [BS]
    include: ['assets']
    exclude: ['\\(net\\)', 'goodwill']
"""


@pytest.mark.criterion("C2")
def test_boundary_violations_by_hand(tmp_path: Path):
    # oracle: TABLE's rule by hand — X's 'repayment' and require 'total', Y's 'goodwill' are
    # unbounded; '\bcost', '^sales' and '\(net\)' are bounded; include patterns are not
    # checked; sorted by (family, field, pattern)
    p = tmp_path / "rules.yaml"
    p.write_text(SYN, encoding="utf-8")
    got = boundary_violations(load_statement_rules(p))
    assert [(v["family"], v["field"], v["pattern"]) for v in got] == [
        ("X", "exclude", "repayment"), ("X", "require", "total"), ("Y", "exclude", "goodwill")]


@pytest.mark.criterion("C2")
def test_r2_has_the_known_inside_word_patterns_and_the_live_rules_none(kit):
    # oracle: the defect by hand — r2's Capex exclude 'repayment' matches inside
    # "Prepayments"; DebtIssued/DebtRepaid 'costs?\b' are unbounded by TABLE's rule; '\bleases?\b'
    # and '^proceeds' are bounded. Requirement 1.2 — the live rules of every family except
    # Debt and DeferredRevenue have no unbounded exclude or require pattern. Red reason
    # before the rules change: Capex 'repayment'.
    assert re.search("repayment", "Prepayments for property and equipment", re.IGNORECASE)
    r2 = {(v["family"], v["field"], v["pattern"])
          for v in boundary_violations(load_statement_rules(kit.r2_rules))}
    assert {("Capex", "exclude", "repayment"), ("DebtIssued", "exclude", r"costs?\b"),
            ("DebtRepaid", "exclude", r"costs?\b")} <= r2
    assert ("Capex", "exclude", r"\bleases?\b") not in r2
    assert ("Capex", "exclude", "^proceeds") not in r2
    live = load_statement_rules(kit.live_rules)
    scope = {f: r for f, r in live.items() if f not in kit.untouched}
    assert len(scope) == 49
    assert boundary_violations(scope) == []
