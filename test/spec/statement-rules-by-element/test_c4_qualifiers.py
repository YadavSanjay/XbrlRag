"""C4: with `strip_qualifiers`, a rule's label patterns see only the label's head.

Oracle: requirement 4 of docs/specs/statement-rules-by-element.md, and TSLA's FY2025 10-K
consolidated statements of cash flows (USD millions, read by hand from tsla-20251231.htm):
"Purchases of property and equipment excluding finance leases, net of sales" (8,527) 2025,
(11,342) 2024, (8,899) 2023; FSDS report 9 line 20, us-gaap
PaymentsToAcquirePropertyPlantAndEquipment, a negating line (values tagged positive).
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.keys.statement_witness import line_is_candidate, select_lines

# tolerance: filed integer amounts in millions; rel 1e-12 absorbs binary floating point only.
REL = 1e-12
PPE = "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment"
CAPEX_LABEL = "Purchases of property and equipment excluding finance leases, net of sales"


def _rule(include, exclude=(), require=(), strip=True) -> dict:
    rule = {"statements": ["BS"], "parenthetical": False, "include": list(include),
            "require": list(require), "exclude": list(exclude), "section": None, "prefer": [],
            "combine": "one", "substitutes": {}}
    if strip:
        rule["strip_qualifiers"] = True
    return rule


@pytest.mark.criterion("C4")
def test_tsla_capex_line_is_witnessed_once_qualifiers_are_stripped(kit):
    # oracle: TSLA FY2025 cash flows, hand-checked (module docstring). The live Capex rule
    # rejects the line ("\bleases?\b" in "excluding finance leases"); with strip_qualifiers
    # the head "Purchases of property and equipment" passes and the line is the only one
    # selected ("Acquisitions of property and equipment included in liabilities" is still
    # rejected by "included in liabilities", which is no qualifier)
    e = kit.env()
    # (the live rule is taken without `strip_qualifiers`, which capex-strip-qualifiers adds)
    live = {k: v for k, v in e.rules["Capex"].items() if k != "strip_qualifiers"}
    stripped = {**live, "strip_qualifiers": True}
    assert line_is_candidate(live, CAPEX_LABEL, PPE) is False
    assert line_is_candidate(stripped, CAPEX_LABEL, PPE) is True
    for year, value in ((2025, 8_527e6), (2024, 11_342e6), (2023, 8_899e6)):
        got = select_lines(e.tables, stripped, kit.TSLA, f"{year}-01-01", f"{year}-12-31", "USD")
        assert [(ln["concept"], ln["stmt"], ln["report"], ln["line"]) for ln in got] == [
            (PPE, "CF", 9, 20)]
        assert got[0]["value"] == pytest.approx(value, rel=REL)
        assert got[0]["negating"] is True


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_a_borrowing_named_only_in_a_qualifier_is_not_debt(kit):
    # oracle: requirement 4 (metamorphic) on TSM's balance sheet: line 45 "Lease liabilities"
    # (28,755.3) relabelled "Lease liabilities, excluding bank loans" names a borrowing only
    # inside its qualifier. The live Debt rule takes it (include "bank loans"; the lease
    # exclusion spares a lease line naming a loan); with strip_qualifiers the head "Lease
    # liabilities" is no debt line, and the two borrowing lines stay. The live rule is taken
    # without `elements` (debt-ifrs-elements adds them; this test is about labels only)
    e = kit.env()
    t = kit.tables_copy()
    kit.set_pre(t, kit.TSM, 45, plabel="Lease liabilities, excluding bank loans")
    live = {k: v for k, v in e.rules["Debt"].items() if k != "elements"}
    stripped = {**live, "strip_qualifiers": True}
    assert line_is_candidate(live, "Lease liabilities, excluding bank loans",
                             "ifrs-full:NoncurrentLeaseLiabilities") is True
    assert line_is_candidate(stripped, "Lease liabilities, excluding bank loans",
                             "ifrs-full:NoncurrentLeaseLiabilities") is False
    got = select_lines(t, stripped, kit.TSM, None, "2024-12-31", "TWD")
    assert [(ln["line"], ln["plabel"]) for ln in got] == [
        (42, "Bonds payable"), (43, "Long-term bank loans")]
    assert sum(ln["value"] for ln in got) == pytest.approx(958_428.9e6, rel=REL)


FILLER = ["purchases", "of", "property", "and", "equipment", "total", "the", "current",
          "portion", "senior", "cash", "payments", "amounts", "other"]
CUES = ["excluding", "exclusive of", "not including", "other than", "except", "net of"]
words = st.lists(st.sampled_from(FILLER), min_size=1, max_size=5).map(" ".join)
forms = st.sampled_from(["{h} {c} {t}", "{h}, {c} {t}", "{h} ({c} {t})"])


@pytest.mark.criterion("C4")
@given(head=words, tail=words, cue=st.sampled_from(CUES), form=forms)
def test_a_term_only_in_a_qualifier_neither_includes_nor_rejects(head, tail, cue, form):
    # oracle: requirement 4 (property) - with strip_qualifiers, an include term only inside a
    # qualifier never makes the line the family's ("debt" negated is not debt), and an
    # exclude term only inside one never rejects it; the same term in the head does both
    neg_include = _rule(["\\bdebt\\b"])
    assert line_is_candidate(neg_include, form.format(h=head, c=cue, t=tail + " debt"), "ext:X") \
        is False
    assert line_is_candidate(neg_include, form.format(h=head + " debt", c=cue, t=tail),
                             "ext:X") is True
    neg_exclude = _rule(["\\bpurchases\\b|\\bpayments\\b|\\bamounts\\b|\\bcash\\b|\\btotal\\b"
                         "|\\bequipment\\b|\\bsenior\\b|\\bproperty\\b|\\bportion\\b|\\bthe\\b"
                         "|\\bcurrent\\b|\\bof\\b|\\band\\b|\\bother\\b"], ["\\bleases?\\b"])
    assert line_is_candidate(neg_exclude, form.format(h=head, c=cue, t=tail + " leases"),
                             "ext:X") is True
    assert line_is_candidate(neg_exclude, form.format(h=head + " leases", c=cue, t=tail),
                             "ext:X") is False


@pytest.mark.criterion("C4")
@given(head=words, tail=words, cue=st.sampled_from(CUES), form=forms,
       req=st.sampled_from([("\\bsenior\\b", "senior"), ("\\bcash\\b", "cash")]))
def test_require_is_tested_on_the_head_too(head, tail, cue, form, req):
    # oracle: requirement 4 (property) - require patterns must match the head: a required
    # term only inside a qualifier does not satisfy them; in the head it does
    pattern, word = req
    rule = _rule(["."], require=[pattern])
    clean_head = " ".join(w for w in head.split() if w != word) or "total"
    clean_tail = " ".join(w for w in tail.split() if w != word) or "total"
    assert line_is_candidate(rule, form.format(h=clean_head, c=cue, t=clean_tail + " " + word),
                             "ext:X") is False
    assert line_is_candidate(rule, form.format(h=clean_head + " " + word, c=cue, t=clean_tail),
                             "ext:X") is True
