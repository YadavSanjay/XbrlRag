"""Spec tests for sign-and-nil C1: a lookup never answers with a nil.

docs/specs/sign-and-nil.md, requirement R1. The lookup's own `_build_result` runs over rows
as its SQL returns them (conftest.py); nothing in the product is faked.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.xbrl.xbrl_factstore_lookup import LookupStatus

OPSEG_DIMS = {"srt:ConsolidationItemsAxis": "us-gaap:OperatingSegmentsMember"}


def _nil_note(result) -> str:
    return " ".join(n for n in result.resolution_notes if "nil" in n)


@pytest.mark.criterion("C1")
@pytest.mark.parametrize("case", ["tax", "interest", "interest_with_valued_prior"])
def test_vst_filed_nil_is_not_found(fx, case):
    # oracle: the iXBRL source vistra-20251231.htm (SHA-256 958c23a6...) files these facts
    # as <ix:nonFraction ... xsi:nil="true"></ix:nonFraction> with no content (f-3006,
    # f-3132, f-3000, f-3066); R1.3 by hand: nothing valued at the FY2025 column -> NOT_FOUND
    if case == "tax":
        rows, family, nil_id = fx.VST_TAX_OPSEG, "TaxExpense", 146227
    elif case == "interest":
        rows, family, nil_id = fx.VST_INTEREST_OPSEG, "InterestExpense", 146221
    else:
        # synthetic on real: the FY2024 column given the consolidated 900,000,000 (fact
        # 143321); R1.1 keeps the answer period at FY2025, so it never answers
        rows = [fx.VST_INTEREST_OPSEG[0], fx.VST_INTEREST_FY2024]
        family, nil_id = "InterestExpense", 146221
    result = fx.run_lookup(rows, family, OPSEG_DIMS)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.value is None
    assert str(nil_id) in _nil_note(result)


@pytest.mark.criterion("C1")
def test_a_nil_copy_never_displaces_the_value(fx):
    # oracle: f-87 prints "179" (income tax expense, in brackets on the statement) for
    # fact 143329, tagged +179,000,000; R1.2: a nil row at the same column is not a value
    only_nil = fx.run_lookup([fx.VST_TAX_OPSEG[0]], "TaxExpense", OPSEG_DIMS)
    assert only_nil.status is LookupStatus.NOT_FOUND
    nil_copy = dict(fx.VST_TAX_FY2025, fact_id=999001, value=None, decimals=None)
    result = fx.run_lookup([nil_copy, fx.VST_TAX_FY2025], "TaxExpense")
    assert result.status is LookupStatus.OK
    # abs=0.5: the stored value is an integer and no arithmetic is done on it
    assert result.value == pytest.approx(179000000, abs=0.5)
    assert result.citation.fact_id == 143329


# Property: random rows of one family over three fiscal-year columns.
CONCEPTS = [("IncomeTaxExpenseBenefit", 1), ("IncomeTaxesPaidNet", 2)]
ENDS = ["2024-01-01", "2025-01-01", "2026-01-01"]
STARTS = {"2024-01-01": "2023-01-01", "2025-01-01": "2024-01-01",
          "2026-01-01": "2025-01-01"}


@st.composite
def _rows(draw):
    """Valued rows in earlier columns; only nil rows (>= 1) in the latest one."""
    latest = draw(st.sampled_from(ENDS[1:]))
    earlier = [e for e in ENDS if e < latest]
    rows, fid = [], 1000
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        fid += 1
        concept, rank = draw(st.sampled_from(CONCEPTS))
        end = draw(st.sampled_from(earlier))
        value = float(draw(st.integers(min_value=-10**12, max_value=10**12)))
        rows.append((fid, concept, rank, value, -6, end))
    nil_ids = []
    for _ in range(draw(st.integers(min_value=1, max_value=3))):
        fid += 1
        concept, rank = draw(st.sampled_from(CONCEPTS))
        rows.append((fid, concept, rank, None, None, latest))
        nil_ids.append(fid)
    order = draw(st.permutations(range(len(rows))))
    return [rows[i] for i in order], nil_ids, latest


def _build(fx, spec):
    return [fx.fact_row(fid, fx.G25 + concept, value, dec, STARTS[end], end, "TaxExpense",
                        rank)
            for fid, concept, rank, value, dec, end in spec]


@pytest.mark.criterion("C1")
@given(_rows(), st.integers(min_value=1, max_value=10**12), st.integers(min_value=0, max_value=3))
def test_nil_only_column_is_not_found_and_nils_never_change_an_answer(fx, drawn, magnitude,
                                                                      extra_nils):
    # oracle: R1 written out — the answer period is the latest column; nothing valued
    # there gives NOT_FOUND naming every nil; nil rows beside a value change nothing
    spec, nil_ids, latest = drawn
    result = fx.run_lookup(_build(fx, spec), "TaxExpense")
    assert result.status is LookupStatus.NOT_FOUND
    assert result.value is None
    note = _nil_note(result)
    for fid in nil_ids:
        assert str(fid) in note

    # metamorphic: a resolvable lookup (one valued rank-1 fact in the latest column) keeps
    # its status, value and fact id when nil rows are added to that column
    base_spec = [s for s in spec if s[3] is not None] + [
        (9000, "IncomeTaxExpenseBenefit", 1, float(magnitude), -6, latest)]
    base = fx.run_lookup(_build(fx, base_spec), "TaxExpense")
    nils = [(9100 + i, CONCEPTS[i % 2][0], CONCEPTS[i % 2][1], None, None, latest)
            for i in range(extra_nils)]
    more = fx.run_lookup(_build(fx, base_spec + nils), "TaxExpense")
    # abs=0.5: the stored value is an integer and no arithmetic is done on it
    assert base.value == pytest.approx(magnitude, abs=0.5)
    assert more.value == pytest.approx(base.value, abs=0.5)
    assert more.status is base.status
    assert more.citation.fact_id == base.citation.fact_id == 9000
