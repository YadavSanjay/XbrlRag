"""C1: only a case with key inputs and no expected fact ids is v2-grounded."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.grounding_v2 import (
    attach_v2_grounding,
    grade_v2_grounding,
    v2_grounding_slots,
)

INTC = 50863
EXACT = 1e-12  # hand counts of small integers: exact up to float rounding


def _lookup_input(gb):
    return gb.inp(INTC, "us-gaap:OperatingIncomeLoss", "USD", "2023-01-01", "2023-12-30",
                  93000000, accession="0000050863-26-000011")


def _fact(gb, fid=1):
    return gb.rec(fid, INTC, "OperatingIncomeLoss", "USD", "2023-01-01", "2023-12-30",
                  93000000, "0000050863-26-000011")


@pytest.mark.criterion("C1")
def test_out_of_scope_cases_are_not_v2_grounded(gb):
    # oracle: requirement 1, by hand — no key, empty inputs, or a v1 label each opt out
    no_key = gb.case("c-nokey", [], key=None)
    empty = gb.case("c-empty", [])
    labelled = gb.case("c-v1", [_lookup_input(gb)], expected_fact_ids=[1])
    facts = {1: _fact(gb)}
    for c in (no_key, empty, labelled):
        assert v2_grounding_slots(c) == []
        assert grade_v2_grounding(c, [1], facts) is None
    # control: the same input without the v1 label is in scope, and the cited fact is its own
    v2 = gb.case("c-v2", [_lookup_input(gb)])
    assert len(v2_grounding_slots(v2)) == 1
    assert grade_v2_grounding(v2, [1], facts)["precision"] == pytest.approx(1.0, abs=EXACT)


@pytest.mark.criterion("C1")
def test_attach_leaves_a_v1_result_untouched(gb):
    # oracle: requirement 1 — the v1 grader's 0.75 / 1.0 stay as they were, uncounted;
    # only the v2 result beside it is counted and set (its one cited fact is the key's)
    v1 = gb.case("c-v1", [_lookup_input(gb)], expected_fact_ids=[1])
    r = gb.result(v1, [1, 2], precision=0.75, recall=1.0)
    v2 = gb.result(gb.case("c-v2", [_lookup_input(gb)]), [1])
    store = gb.FakeFactStore({1: _fact(gb, 1), 2: _fact(gb, 2)})
    assert attach_v2_grounding([r, v2], store) == 1
    assert (r.grade.grounding_precision, r.grade.grounding_recall) == (0.75, 1.0)
    assert (v2.grade.grounding_precision, v2.grade.grounding_recall) == (1.0, 1.0)


@pytest.mark.criterion("C1")
@pytest.mark.metamorphic
@given(
    precision=st.one_of(st.none(), st.floats(0, 1)),
    recall=st.one_of(st.none(), st.floats(0, 1)),
    ids=st.lists(st.integers(1, 5), max_size=5),
    labels=st.lists(st.integers(1, 5), min_size=1, max_size=3),
)
def test_attach_never_moves_a_labelled_result(gb, precision, recall, ids, labels):
    # oracle: requirement 1 — whatever a v1 result carries and cites, attaching keeps it
    v1 = gb.case("c-v1", [_lookup_input(gb)], expected_fact_ids=labels)
    r = gb.result(v1, ids, precision=precision, recall=recall)
    v2 = gb.result(gb.case("c-v2", [_lookup_input(gb)]), [1])
    store = gb.FakeFactStore({i: _fact(gb, i) for i in range(1, 6)})
    assert attach_v2_grounding([v2, r], store) == 1
    assert (r.grade.grounding_precision, r.grade.grounding_recall) == (precision, recall)
    assert (v2.grade.grounding_precision, v2.grade.grounding_recall) == (1.0, 1.0)
