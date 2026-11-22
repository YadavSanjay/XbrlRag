"""C9: attaching grounding to graded results, live or rebuilt from a stored payload."""

from __future__ import annotations

import copy
import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import checkpoint_payload
from src.eval.core.graders.base import Grade
from src.eval.core.graders.grounding_v2 import attach_v2_grounding
from src.eval.core.report import CaseResult, Metrics

AAA = 1000
BBB = 2000
FY = ("2025-01-01", "2025-12-31")


def _fixture(gb):
    lookup = gb.case("c-lookup", [gb.inp(AAA, "us-gaap:Revenues", "USD", *FY, 1.0e11)])
    ratio = gb.case("c-ratio", [gb.inp(BBB, "us-gaap:NetIncomeLoss", "USD", *FY, 4.0e9),
                                gb.inp(BBB, "us-gaap:Revenues", "USD", *FY, 5.0e10)],
                    shape="compute.ratio", kind="compute")
    v1 = gb.case("c-v1", [gb.inp(AAA, "us-gaap:Revenues", "USD", *FY, 1.0e11)],
                 expected_fact_ids=[1])
    facts = {
        1: gb.rec(1, AAA, "Revenues", "USD", *FY, 1.0e11),
        2: gb.rec(2, AAA, "CostOfRevenue", "USD", *FY, 6.0e10),  # unrelated
        3: gb.rec(3, BBB, "NetIncomeLoss", "USD", *FY, 4.0e9),
        4: gb.rec(4, BBB, "Revenues", "USD", *FY, 4.9e10),  # off by 1 billion: wrong
    }
    results = [
        gb.result(lookup, [1, 2], grade=Grade.CORRECT),
        gb.result(ratio, [3, 4], grade=Grade.INCORRECT),
        gb.result(v1, [1], grade=Grade.CORRECT, precision=1.0, recall=1.0),
    ]
    return results, facts


@pytest.mark.criterion("C9")
def test_attach_sets_hand_counted_values(gb):
    # oracle: requirements 12-13 by hand — lookup 1/2 right, slot evidenced: 0.5 / 1.0; ratio
    # 1/2 right, one of two slots: 0.5 / 0.5; v1 untouched 1.0 / 1.0; run mean over three
    # cases (0.5 + 0.5 + 1.0) / 3 and recall (1.0 + 0.5 + 1.0) / 3
    results, facts = _fixture(gb)
    before = [(r.grade.grade, r.grade.passed, r.grade.value_correct) for r in results]
    assert attach_v2_grounding(results, gb.FakeFactStore(facts)) == 2
    got = [(r.grade.grounding_precision, r.grade.grounding_recall) for r in results]
    assert got == [(0.5, 1.0), (0.5, 0.5), (1.0, 1.0)]
    assert [(r.grade.grade, r.grade.passed, r.grade.value_correct) for r in results] == before
    metrics = Metrics.compute(results)
    assert metrics.grounding_precision == pytest.approx(2.0 / 3, rel=1e-12)  # float mean
    assert metrics.grounding_recall == pytest.approx(2.5 / 3, rel=1e-12)  # float mean


@pytest.mark.criterion("C9")
def test_a_stored_payload_grades_the_same(gb):
    # oracle: requirement 12 — the stored form keeps produced.fact_ids, so a rebuilt result
    # (JSON round trip, as the store writes it) gets the hand-counted 0.5 / 0.5 as well
    results, facts = _fixture(gb)
    stored = json.loads(json.dumps(checkpoint_payload(results[1])))
    rebuilt = CaseResult.from_dict(stored, results[1].case)
    assert rebuilt.grade.grounding_precision is None  # graded before this change
    assert attach_v2_grounding([rebuilt], gb.FakeFactStore(facts)) == 1
    assert (rebuilt.grade.grounding_precision, rebuilt.grade.grounding_recall) == (0.5, 0.5)
    assert rebuilt.grade.grade is Grade.INCORRECT


@pytest.mark.criterion("C9")
@pytest.mark.metamorphic
@given(st.lists(st.integers(1, 4), max_size=4), st.lists(st.integers(1, 4), max_size=4))
def test_attaching_twice_is_attaching_once(gb, lookup_ids, ratio_ids):
    # oracle: requirement 12 — a pure function of the case, its fact ids and the store; the
    # lookup's precision is its right ids over its distinct ids (only id 1 is right for it)
    results, facts = _fixture(gb)
    results[0].grade.produced["fact_ids"] = list(lookup_ids)
    results[1].grade.produced["fact_ids"] = list(ratio_ids)
    store = gb.FakeFactStore(facts)
    assert attach_v2_grounding(results, store) == 2
    once = copy.deepcopy([(r.grade.grounding_precision, r.grade.grounding_recall)
                          for r in results])
    assert attach_v2_grounding(results, store) == 2
    assert [(r.grade.grounding_precision, r.grade.grounding_recall) for r in results] == once
    distinct = set(lookup_ids)
    expected = None if not distinct else (1 if 1 in distinct else 0) / len(distinct)
    assert once[0][0] == expected
    assert once[0][1] == (1.0 if 1 in distinct else 0.0)
