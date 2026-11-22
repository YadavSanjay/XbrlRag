"""Spec tests for grader-nested-results: the numeric grader reads a decomposed
answer's sub-question steps and flattens nested component ids
(docs/specs/grader-nested-results.md). Pure: response dicts, no DB.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.numeric import (
    all_fact_ids,
    all_numeric_values,
    component_fact_ids,
    execution_steps,
    has_fact_citation,
    lookup_step_values,
)


def _lookup(value, fact_id, status="OK"):
    return {"op": "lookup", "status": status, "value": value,
            "citation": {"fact_id": fact_id}}


def _steps_oracle(er):
    """C1 written out."""
    if not isinstance(er, dict):
        return []
    out = list(er["steps"]) if isinstance(er.get("steps"), list) else []
    subs = er.get("subqueries")
    if isinstance(subs, dict):
        for sub in subs.values():
            out.extend(_steps_oracle(sub))
    return out


def _ids_oracle(value):
    """C2 written out."""
    if value is None:
        return []
    items = list(value.values()) if isinstance(value, dict) else (
        list(value) if isinstance(value, (list, tuple)) else [value])
    out = []
    for item in items:
        out.extend(item if isinstance(item, (list, tuple)) else [item])
    return out


# NVIDIA FY2025 "operating income for Graphics Segment and Operating Segments",
# decomposed in eval-9a74fa9183cf: sq1 resolved fact 65496, sq2 was AMBIGUOUS.
NVDA_DECOMPOSED = {"execution_result": {"subqueries": {
    "sq1": {"steps": [_lookup(5_085_000_000.0, 65496)]},
    "sq2": {"steps": [_lookup(5_085_000_000.0, 65496, status="AMBIGUOUS")]},
}}}


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_steps_point_cases():
    # oracle: the definition, by hand
    a, b, c = {"id": "a"}, {"id": "b"}, {"id": "c"}
    assert execution_steps({"steps": [a]}) == [a]
    assert execution_steps({"subqueries": {"sq1": {"steps": [a]},
                                           "sq2": {"steps": [b, c]}}}) == [a, b, c]
    assert execution_steps({"steps": [a], "subqueries": {"sq1": None}}) == [a]
    assert execution_steps(None) == []
    nested = {"subqueries": {"sq1": {"subqueries": {"x": {"steps": [b]}}, "steps": [a]}}}
    assert execution_steps(nested) == [a, b]


step_st = st.integers(0, 99).map(lambda i: {"id": i})
leaf = st.one_of(st.none(), st.just({}),
                 st.lists(step_st, max_size=3).map(lambda s: {"steps": s}))
er_st = st.recursive(
    leaf,
    lambda children: st.builds(
        lambda s, subs: {"steps": s, "subqueries": subs},
        st.lists(step_st, max_size=2),
        st.dictionaries(st.sampled_from(["sq1", "sq2", "sq3"]), children, max_size=3)),
    max_leaves=5)


@pytest.mark.criterion("C1")
@given(er_st)
def test_steps_match_the_rule(er):
    # oracle: C1 written out (`_steps_oracle`); a fixed decomposed result anchors it
    assert execution_steps(er) == _steps_oracle(er)
    assert execution_steps({"subqueries": {"s": {"steps": [{"id": 1}]}}}) == [{"id": 1}]


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_component_ids_point_cases():
    # oracle: the definition, by hand
    assert component_fact_ids({"revenue": 19102, "cost_of_revenue": [19111, 19114]}) == [
        19102, 19111, 19114]
    assert component_fact_ids([1, [2, 3]]) == [1, 2, 3]
    assert component_fact_ids({"a": 1, "b": 2}) == [1, 2]
    assert component_fact_ids(7) == [7]
    assert component_fact_ids(None) == []


comp_st = st.one_of(
    st.none(), st.integers(0, 10**6),
    st.dictionaries(st.text(max_size=4),
                    st.one_of(st.integers(0, 10**6),
                              st.lists(st.integers(0, 10**6), max_size=3)), max_size=4),
    st.lists(st.one_of(st.integers(0, 10**6), st.lists(st.integers(0, 10**6), max_size=3)),
             max_size=4))


@pytest.mark.criterion("C2")
@given(comp_st)
def test_component_ids_match_the_rule(value):
    # oracle: C2 written out (`_ids_oracle`); a fixed nested object anchors it
    assert component_fact_ids(value) == _ids_oracle(value)
    assert component_fact_ids({"r": 1, "c": [2, 3]}) == [1, 2, 3]


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_decomposed_and_nested_responses_are_graded():
    # oracle: the definitions by hand — sq1's OK lookup is visible; the AMBIGUOUS sq2 is not
    assert lookup_step_values(NVDA_DECOMPOSED) == [5_085_000_000.0]
    assert all_fact_ids(NVDA_DECOMPOSED) == [65496]
    assert has_fact_citation(NVDA_DECOMPOSED) is True
    gm = {"execution_result": {"steps": [{
        "op": "compute", "status": "OK", "value": 0.4935,
        "component_fact_ids": {"revenue": 19102, "cost_of_revenue": [19111, 19114]}}]}}
    assert all_fact_ids(gm) == [19102, 19111, 19114]
    screen = {"execution_result": {"steps": [{
        "op": "screen", "status": "OK", "rows": [
            {"fact_id": None, "component_fact_ids": {"revenue": 1, "cost_of_revenue": [2, 3]}}]}]}}
    assert all_fact_ids(screen) == [1, 2, 3]
    plain = {"execution_result": {"steps": [_lookup(10.0, 5)]}}
    assert lookup_step_values(plain) == [10.0] and all_fact_ids(plain) == [5]


@pytest.mark.criterion("C3")
@given(st.lists(st.tuples(st.floats(min_value=-1e12, max_value=1e12, allow_nan=False),
                          st.integers(1, 10**6),
                          st.sampled_from(["OK", "AMBIGUOUS", "NOT_FOUND"])),
                min_size=1, max_size=4))
def test_decomposed_values_are_the_ok_sub_steps(sub_steps):
    # oracle: the definition — exactly the OK sub-steps' values and ids are graded
    resp = {"execution_result": {"subqueries": {
        f"sq{i}": {"steps": [_lookup(v, f, s)]} for i, (v, f, s) in enumerate(sub_steps)}}}
    ok = [(v, f) for v, f, s in sub_steps if s == "OK"]
    assert lookup_step_values(resp) == [v for v, _ in ok]
    assert all_fact_ids(resp) == list(dict.fromkeys(f for _, f in ok))
    assert lookup_step_values(NVDA_DECOMPOSED) == [5_085_000_000.0]


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.lists(st.tuples(st.floats(min_value=-1e9, max_value=1e9, allow_nan=False),
                          st.integers(1, 10**6)), min_size=1, max_size=4),
       st.randoms(use_true_random=False))
def test_wrapping_and_order_do_not_matter(pairs, rnd):
    # oracle: the C4 properties, checked directly
    steps = [_lookup(v, f) for v, f in pairs]
    plain = {"execution_result": {"steps": steps}}
    wrapped = {"execution_result": {"subqueries": {"sq1": {"steps": steps}}}}
    assert all_numeric_values(wrapped) == all_numeric_values(plain)
    assert all_fact_ids(wrapped) == all_fact_ids(plain)
    subs = [(f"sq{i}", {"steps": [s]}) for i, s in enumerate(steps)]
    shuffled = list(subs)
    rnd.shuffle(shuffled)
    a = {"execution_result": {"subqueries": dict(subs)}}
    b = {"execution_result": {"subqueries": dict(shuffled)}}
    assert sorted(all_numeric_values(a)) == sorted(all_numeric_values(b))
    assert set(all_fact_ids(a)) == set(all_fact_ids(b))


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.dictionaries(st.text(max_size=4), st.integers(0, 10**6), min_size=1, max_size=5))
def test_scalar_objects_keep_the_old_behaviour(obj):
    # oracle: the definition — an object of scalars yields its values, as before
    assert component_fact_ids(obj) == list(obj.values())


@pytest.mark.criterion("C4")
def test_properties_point_case():
    # oracle: the C4 properties by hand on NVIDIA — wrapping a plain result changes nothing
    plain = {"execution_result": {"steps": [_lookup(5_085_000_000.0, 65496)]}}
    wrapped = {"execution_result": {"subqueries": {"sq1": {"steps": [
        _lookup(5_085_000_000.0, 65496)]}}}}
    assert all_fact_ids(wrapped) == all_fact_ids(plain) == [65496]
