"""C6: a compound query's final answer: every surviving id resolves in its own sub-query (R2).

The real pipeline (decomposer, router, planner, executor, calculator, synthesizer, verifier,
merge) answers ``COMPOUND``; its LLM stages are scripted and the fact store is fake (conftest
``Compound``). Sub-query 1 screens FY2024 revenue (WMT, AMZN, AAPL); sub-query 2 depends on it
through ``<<TOP:sq1:2>>`` and computes WMT's and AMZN's net margins from four lookups.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

PREFIX = "failed faithfulness check:"


def _resolves(cited: str, steps: list[dict], chunk_ids: set[str], *, chunk: bool) -> bool:
    """Rule 1, written independently of the code under test."""
    if chunk:
        return cited in chunk_ids
    for s in steps:
        if s.get("op") == "narrative":
            continue
        if str(s.get("step_id")) == cited:
            return True
        if str((s.get("citation") or {}).get("fact_id")) == cited:
            return True
        if any(str(r.get("fact_id")) == cited for r in s.get("rows") or []):
            return True
        if any(str(v) == cited for v in (s.get("component_fact_ids") or {}).values()):
            return True
    return False


def _assert_every_id_resolves_in_its_sub_query(answer) -> None:
    chunk_ids = {c["chunk_id"] for c in answer.narrative_chunks}
    for d in answer.answer_details:
        assert "subquery_id" in d, d
        steps = answer.execution_result["subqueries"][d["subquery_id"]]["steps"]
        for f in d["fact_ids"]:
            assert _resolves(str(f), steps, chunk_ids, chunk=False), (d, f)
        for c in d["chunk_ids"]:
            assert _resolves(str(c), steps, chunk_ids, chunk=True), (d, c)


@pytest.mark.criterion("C6")
def test_the_compound_answer_is_the_hand_built_one(ci):
    # oracle: Compound.SQ1_DETAILS / SQ2_DETAILS — each statement's keep/drop decided by hand
    # under rules 1-3 (reasons in the conftest comments); kept ones gain their subquery_id
    kit = ci.Compound()
    answer = kit.pipeline.generate(ci.COMPOUND)

    assert answer.answer_details == [
        {"statement": "Walmart ranked first with fiscal 2024 revenue of $648.1 billion.",
         "fact_ids": ["205006"], "chunk_ids": [], "subquery_id": "sq1"},
        {"statement": "Amazon was second with revenue of $637,959 million.",
         "fact_ids": ["6854"], "chunk_ids": [], "subquery_id": "sq1"},
        {"statement": "Walmart's net margin was 2.4%.",
         "fact_ids": ["m_wmt"], "chunk_ids": [], "subquery_id": "sq2"},
        {"statement": "Amazon's net margin was 9.3%, on net income of $59.2 billion.",
         "fact_ids": ["m_amzn"], "chunk_ids": [], "subquery_id": "sq2"},
    ]
    reasons = {p["question_fragment"]: p["reason"] for p in answer.unanswered_parts}
    assert set(reasons) == {
        "Apple ranked third at $391.0 billion.",
        "Amazon's revenue was $648.1 billion.",
        "Apple's revenue was $391.0 billion.",
        "Walmart had the highest revenue of the companies screened.",
        "Amazon's net margin exceeded Walmart's by 6.9 percentage points.",
    }
    assert all(r.startswith(PREFIX) for r in reasons.values())
    assert str(ci.INVENTED) in reasons["Apple ranked third at $391.0 billion."]
    assert "648.1" in reasons["Amazon's revenue was $648.1 billion."]
    assert "6.9" in reasons["Amazon's net margin exceeded Walmart's by 6.9 percentage points."]
    _assert_every_id_resolves_in_its_sub_query(answer)


@pytest.mark.criterion("C6")
def test_sub_query_two_was_answered_for_the_screened_top_two(ci):
    # oracle: the dependency — sub-query 2's lookups ran for WMT and AMZN, the screen's top two
    kit = ci.Compound()
    answer = kit.pipeline.generate(ci.COMPOUND)
    sq2 = answer.execution_result["subqueries"]["sq2"]["steps"]
    margins = {s["step_id"]: s["value"] for s in sq2 if s["op"] == "compute"}
    # closed form from the real facts: net income / revenue
    assert margins == pytest.approx({"m_wmt": 15_511 / 648_125, "m_amzn": 59_248 / 637_959},
                                    rel=1e-12)  # float division of the same operands
    assert [d.get("subquery_id") for d in answer.answer_details] == ["sq1", "sq1", "sq2", "sq2"]


@pytest.mark.criterion("C6")
@settings(max_examples=30)
@given(order1=st.permutations(range(3)), order2=st.permutations(range(6)))
def test_statement_order_never_changes_which_survive(ci, order1, order2):
    # oracle: the keep/drop of each statement is fixed by hand (Compound); permuting the
    # synthesizer's statements permutes the survivors and nothing else
    kit = ci.Compound(order1=list(order1), order2=list(order2))
    answer = kit.pipeline.generate(ci.COMPOUND)
    assert answer.answer_details == kit.expected
    assert sorted(p["question_fragment"] for p in answer.unanswered_parts) == sorted(kit.dropped)
    _assert_every_id_resolves_in_its_sub_query(answer)
