"""C4: breakdown amounts the expected decomposition cannot account for are reported."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.breakdown import compare_breakdown
from src.eval.core.graders.honesty import unexplained_breakdown_values


@pytest.mark.criterion("C4")
def test_hand_worked_components():
    # oracle: hand arithmetic — components 60, 30, 10 (total 100). 60 is a component,
    # 90 = 60 + 30 and 40 = 30 + 10 are subset sums, 100 is the total, 0.6 is a share
    # (abs < 1, never reported). 45 is none of these, so it alone is unexplained.
    expected = [60.0, 30.0, 10.0]
    produced = [60.0, 90.0, 40.0, 100.0, 0.6, 45.0]
    assert unexplained_breakdown_values(expected, produced, 1e-4) == [45.0]

    metrics = compare_breakdown([{"value": v} for v in expected], produced, rel_tol=1e-4)
    assert getattr(metrics, "unexplained_values", None) == [45.0]


@pytest.mark.criterion("C4")
@given(
    components=st.lists(st.integers(min_value=1, max_value=10**9), min_size=1, max_size=6),
    picks=st.lists(st.lists(st.booleans(), min_size=6, max_size=6), min_size=0, max_size=4),
    excess=st.integers(min_value=1, max_value=10**9),
)
def test_subset_sums_are_explained_and_anything_above_the_total_is_not(components, picks, excess):
    # oracle: by construction — every non-empty subset sum of the components is explained,
    # and a value above the total by more than the tolerance exceeds every subset sum, so it
    # cannot be explained. Integers keep the sums exact.
    expected = [float(c) for c in components]
    total = sum(expected)
    sums = []
    for mask in picks:
        chosen = [v for v, keep in zip(expected, mask[: len(expected)], strict=True) if keep]
        if chosen:
            sums.append(float(sum(chosen)))
    stray = total * 1.01 + float(excess)  # > total by 1% plus, far beyond rel_tol=1e-4
    produced = [*sums, stray]

    assert unexplained_breakdown_values(expected, produced, 1e-4) == [stray]


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(st.permutations([60.0, 90.0, 40.0, 100.0, 0.6, 45.0, 1234.0]))
def test_order_of_produced_values_does_not_matter(produced):
    # oracle: metamorphic — a breakdown is a set; permuting the produced values cannot
    # change which ones are unexplained (45 and 1234 against components 60, 30, 10)
    assert unexplained_breakdown_values([60.0, 30.0, 10.0], list(produced), 1e-4) == [45.0, 1234.0]
