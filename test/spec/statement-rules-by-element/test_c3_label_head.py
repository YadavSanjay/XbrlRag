"""C3: `label_head` removes negating qualifiers exactly as requirement 3 defines them.

Oracle: requirement 3 of docs/specs/statement-rules-by-element.md, applied by hand to each
label below (the steps are written out for the first two).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import label_head

CASES = [
    # cue "excluding" -> removed up to ","; cue "net of" -> removed to the end; joined
    # "Purchases of property and equipment , " -> space before "," dropped -> trailing ", "
    # stripped
    ("Purchases of property and equipment excluding finance leases, net of sales",
     "Purchases of property and equipment"),
    # cue inside parentheses -> removed up to ")" -> "Intangible assets, net ()" -> empty
    # parentheses dropped -> whitespace stripped
    ("Intangible assets, net (excluding goodwill)", "Intangible assets, net"),
    ("Research and Development Expense (Excluding Acquired in Process Cost)",
     "Research and Development Expense"),
    ("Total debt, excluding finance leases", "Total debt"),
    ("Cost of revenue (exclusive of depreciation and amortization shown separately below)",
     "Cost of revenue"),
    ("Taxes other than income taxes", "Taxes"),
    ("Proceeds from issuance of debt, net of issuance costs", "Proceeds from issuance of debt"),
    ("Long-term debt, net of current portion; excluding finance leases", "Long-term debt"),
    ("Long-term debt (net of current portion)", "Long-term debt"),
    ("Senior notes, not including current maturities", "Senior notes"),
    ("Total revenues, except for leasing", "Total revenues"),
    ("Accounts receivable, net of allowance for credit losses of $5 and $4", "Accounts receivable"),
    ("OTHER THAN TEMPORARY IMPAIRMENT LOSSES", ""),
    # no cue: unchanged but for whitespace
    ("Revenues, net", "Revenues, net"),
    ("Net income", "Net income"),
    ("Total  debt ", "Total debt"),
    # a cue word inside a longer word is no cue
    ("Cost excludingdepreciation", "Cost excludingdepreciation"),
    ("Net offset of hedges", "Net offset of hedges"),
]


@pytest.mark.criterion("C3")
@pytest.mark.parametrize(("label", "head"), CASES)
def test_label_head_hand_cases(label, head):
    # oracle: requirement 3 by hand (the table above)
    assert label_head(label) == head


WORDS = ["purchases", "of", "property", "and", "equipment", "total", "debt", "bank", "loans",
         "lease", "liabilities", "finance", "interest", "revenue", "goodwill", "cash", "current",
         "portion", "long-term", "senior", "notes", "sales", "costs", "tax"]
CUES = ["excluding", "Excluding", "exclusive of", "not including", "other than", "except",
        "net of", "NET OF"]
phrase = st.lists(st.sampled_from(WORDS), min_size=1, max_size=6).map(" ".join)


@pytest.mark.criterion("C3")
@given(head=phrase, cue=st.sampled_from(CUES), tail=phrase,
       form=st.sampled_from(["{h} {c} {t}", "{h}, {c} {t}", "{h} ({c} {t})", "{h}; {c} {t}"]))
def test_an_appended_qualifier_is_removed(head, cue, tail, form):
    # oracle: requirement 3 (metamorphic) - a cue-free phrase is its own head; appending a
    # qualifier in any of the four forms leaves the same head
    assert label_head(head) == head
    assert label_head(form.format(h=head, c=cue, t=tail)) == head


@pytest.mark.criterion("C3")
@given(head=phrase, cue=st.sampled_from(CUES), mid=phrase, rest=phrase)
def test_a_qualifier_ends_at_its_terminator(head, cue, mid, rest):
    # oracle: requirement 3 (metamorphic) - a qualifier in parentheses ends at ")", so the
    # text after it stays: "<h> (<cue> <m>) <r>" has head "<h> <r>"
    assert label_head(f"{head} ({cue} {mid}) {rest}") == f"{head} {rest}"
