"""Spec tests for alternative-inputs-grounding: a derived metric's input may be cited
through any of several valid facts (docs/specs/alternative-inputs-grounding.md).

Pure: JSON grouping, class merging, and the unchanged grade_grounding over the result.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.citation import grade_grounding
from src.eval.suites.sql_derived import fact_id_groups_from_json, group_classes


def _ids(item):
    """C1's member rule written out: an int or a digit string, never a bool."""
    if isinstance(item, bool):
        return None
    if isinstance(item, int):
        return item
    if isinstance(item, str) and item.strip().isdigit():
        return int(item)
    return None


def _groups_oracle(value):
    """C1 written out."""
    if isinstance(value, dict):
        entries = list(value.values())
    elif isinstance(value, (list, tuple)):
        entries = list(value)
    else:
        return []
    out = []
    for e in entries:
        if isinstance(e, (list, tuple)):
            members = [m for m in (_ids(x) for x in e) if m is not None]
            if members:
                out.append(members)
        else:
            m = _ids(e)
            if m is not None:
                out.append([m])
    return out


def _classes_oracle(groups, classes):
    """C2 written out."""
    return [sorted({x for g in group for x in classes.get(g, [g])}) for group in groups]


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_groups_point_cases():
    # oracle: the definition, by hand
    assert fact_id_groups_from_json({"revenue": 10, "cost_of_revenue": [11, 12]}) == [
        [10], [11, 12]]
    assert fact_id_groups_from_json([5, "6", [7, "8"]]) == [[5], [6], [7, 8]]
    assert fact_id_groups_from_json({"a": True, "b": "x", "c": []}) == []
    assert fact_id_groups_from_json(None) == []
    assert fact_id_groups_from_json(42) == []


member = st.one_of(st.integers(0, 10**6), st.integers(0, 999).map(str), st.booleans(),
                   st.just("x"), st.none())
entry = st.one_of(member, st.lists(member, max_size=3))
json_values = st.one_of(st.dictionaries(st.text(max_size=4), entry, max_size=4),
                        st.lists(entry, max_size=4), st.none(), st.integers())


@pytest.mark.criterion("C1")
@given(json_values)
def test_groups_match_the_rule(value):
    # oracle: C1 written out (`_groups_oracle`); a fixed real component map anchors it
    assert fact_id_groups_from_json(value) == _groups_oracle(value)
    assert fact_id_groups_from_json({"revenue": 1, "cogs": [2, 3]}) == [[1], [2, 3]]


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_classes_point_case():
    # oracle: the definition, by hand
    assert group_classes([[10], [11, 12]], {10: [10, 99], 11: [11], 12: [12, 13]}) == [
        [10, 99], [11, 12, 13]]
    assert group_classes([[7]], {}) == [[7]]


ids = st.integers(1, 30)
groups_st = st.lists(st.lists(ids, min_size=1, max_size=3), min_size=1, max_size=4)
classes_st = st.dictionaries(ids, st.lists(ids, min_size=1, max_size=3), max_size=10).map(
    lambda d: {k: sorted({k, *v}) for k, v in d.items()})


@pytest.mark.criterion("C2")
@given(groups_st, classes_st)
def test_classes_match_the_rule(groups, classes):
    # oracle: C2 written out (`_classes_oracle`)
    assert group_classes(groups, classes) == _classes_oracle(groups, classes)


# --------------------------------------------------------------------------- C3
REV, COGS, GP = 19100, 19111, 19114  # AMD FY2024: revenue, cost of revenue, gross profit


@pytest.mark.criterion("C3")
def test_either_route_satisfies_grounding():
    # oracle: grade_grounding's class rule by hand — one class per input, any member counts
    groups = fact_id_groups_from_json({"revenue": REV, "cost_of_revenue": [COGS, GP]})
    classes = group_classes(groups, {})
    flat = [REV, COGS, GP]
    for cited in ({REV, GP}, {REV, COGS}):
        r = grade_grounding(flat, sorted(cited), fact_id_classes=classes)
        assert r.satisfied
        assert r.recall == pytest.approx(1.0, abs=1e-12)  # both classes met
    only_revenue = grade_grounding(flat, [REV], fact_id_classes=classes)
    assert not only_revenue.satisfied


@pytest.mark.criterion("C3")
@given(st.sampled_from([COGS, GP]), st.lists(st.integers(1, 99), max_size=3))
def test_any_alternative_plus_equivalents_satisfies(choice, extra_equivalents):
    # oracle: the rule — an equivalent of either alternative also satisfies its input
    eq = {COGS: sorted({COGS, *extra_equivalents}), GP: [GP]}
    classes = group_classes([[REV], [COGS, GP]], eq)
    cited = [REV, choice]
    assert grade_grounding([REV, COGS, GP], cited, fact_id_classes=classes).satisfied
    for alt in extra_equivalents:
        assert grade_grounding([REV, COGS, GP], [REV, alt], fact_id_classes=classes).satisfied


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(groups_st, classes_st, classes_st)
def test_properties(groups, classes, more):
    # oracle: the C4 properties, checked directly
    out = group_classes(groups, classes)
    assert len(out) == len(groups)
    assert all(set(g) <= set(c) for g, c in zip(groups, out, strict=True))
    grown = {**classes, **{k: sorted({*classes.get(k, [k]), *v}) for k, v in more.items()}}
    assert all(set(a) <= set(b)
               for a, b in zip(out, group_classes(groups, grown), strict=True))


@pytest.mark.criterion("C4")
def test_properties_point_case():
    # oracle: the C4 properties by hand on AMD FY2024 — one class per input, members kept,
    # a larger equivalence map only adds ids; a scalar object keeps the old grouping
    out = group_classes([[REV], [COGS, GP]], {GP: [GP, 19200]})
    assert out == [[REV], [COGS, GP, 19200]]
    assert fact_id_groups_from_json({"revenue": REV, "cost_of_revenue": COGS}) == [
        [REV], [COGS]]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.dictionaries(st.text(max_size=4), st.integers(0, 10**6), min_size=1, max_size=5))
def test_scalar_objects_keep_the_old_behaviour(obj):
    # oracle: an object of scalars is one singleton group per id, in order (the old
    # flattening), so today's suites grade the same
    assert fact_id_groups_from_json(obj) == [[v] for v in obj.values()]
