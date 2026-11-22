"""C6: trend (every year required) and point-in-time (both values, each labelled).

Oracle: requirements 12-13 of docs/specs/v2-grading.md and architecture §7. The trend is
FY2023 100m, FY2024 110m, FY2025 121m; the point-in-time pair 100m (as originally reported)
and 104m (latest). Where today's breakdown grader would pass 2 of 3 (coverage 0.67 < 0.8
fails, but 4 of 5 would pass), the trend demands all.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_breakdown
from src.eval.core.graders.v2 import grade_v2

M = 1e6
LABELLED = "As originally reported: 100 million; the latest 10-K shows 104 million."


@pytest.mark.criterion("C6")
def test_trend_every_year(kit):
    # oracle: requirement 12 — each year's value matched once; all three pass, a missing
    # FY2024 fails as incomplete with that year's check false.
    case = kit.case(kit.hx_trend())
    ok = grade_v2(case, kit.answer(values=[121 * M, 100 * M, 110 * M], fact_ids=[31, 32, 33]))
    assert ok.grade is Grade.CORRECT and ok.passed is True
    assert kit.checks(ok) == {"FY2023": True, "FY2024": True, "FY2025": True}

    miss = grade_v2(case, kit.answer(values=[100 * M, 121 * M], fact_ids=[31, 33]))
    assert miss.grade is Grade.INCORRECT and miss.passed is False
    assert miss.incomplete is True
    assert kit.checks(miss) == {"FY2023": True, "FY2024": False, "FY2025": True}

    uncited = grade_v2(case, kit.answer(values=[100 * M, 110 * M, 121 * M]))
    assert uncited.grade is Grade.PARTIAL

    refused = grade_v2(case, kit.answer(text="No data.", refused=True))
    assert refused.grade is Grade.REFUSED


@pytest.mark.criterion("C6")
def test_trend_stricter_than_breakdown_and_values_used_once(kit):
    # oracle: requirement 12 — a five-year trend with four years found passes the legacy
    # breakdown grader (coverage 0.8) but fails the trend; one produced value matches one
    # year only, so 100m twice in the expected rows needs 100m twice.
    rows = [{"entity": f"FY{y}", "value": v * M}
            for y, v in [(2021, 80), (2022, 90), (2023, 100), (2024, 110), (2025, 121)]]
    d = {**kit.hx_trend(), "expected_rows": rows, "expected_fact_ids": [],
         "fact_id_classes": []}
    case = kit.case(d)
    ans = kit.answer(values=[80 * M, 90 * M, 100 * M, 110 * M], fact_ids=[9])
    assert grade_breakdown(case, ans).passed is True
    trend = grade_v2(case, ans)
    assert trend.passed is False
    assert kit.checks(trend)["FY2025"] is False

    flat = [{"entity": "FY2024", "value": 100 * M}, {"entity": "FY2025", "value": 100 * M}]
    flat_case = kit.case({**d, "expected_rows": flat})
    once = grade_v2(flat_case, kit.answer(values=[100 * M], fact_ids=[9]))
    assert kit.checks(once) == {"FY2024": True, "FY2025": False}
    twice = grade_v2(flat_case, kit.answer(values=[100 * M, 100 * M], fact_ids=[9]))
    assert twice.grade is Grade.CORRECT


@pytest.mark.criterion("C6")
def test_point_in_time(kit):
    # oracle: requirement 13 — both values and both labels pass; unlabelled is PARTIAL; one
    # value is INCORRECT and incomplete; equal values are matched by one number.
    case = kit.case(kit.hx_pit())
    ok = grade_v2(case, kit.answer(text=LABELLED, values=[100 * M, 104 * M], fact_ids=[41, 42]))
    assert ok.grade is Grade.CORRECT
    assert kit.checks(ok) == {"as_originally_reported": True, "latest": True, "labels": True}

    bare = grade_v2(case, kit.answer(text="100 million and 104 million.",
                                     values=[100 * M, 104 * M], fact_ids=[41, 42]))
    assert bare.grade is Grade.PARTIAL and bare.passed is False
    assert kit.checks(bare) == {"as_originally_reported": True, "latest": True,
                                "labels": False}

    one = grade_v2(case, kit.answer(text=LABELLED, values=[104 * M], fact_ids=[42]))
    assert one.grade is Grade.INCORRECT and one.incomplete is True
    assert kit.checks(one) == {"as_originally_reported": False, "latest": True,
                               "labels": True}

    same = kit.case(kit.hx_pit(latest=100 * M))
    unchanged = grade_v2(same, kit.answer(
        text="Originally reported and latest figures are both 100 million.",
        values=[100 * M], fact_ids=[41]))
    assert unchanged.grade is Grade.CORRECT
    assert kit.checks(unchanged) == {"as_originally_reported": True, "latest": True,
                                     "labels": True}


@pytest.mark.criterion("C6")
@pytest.mark.metamorphic
@given(perm=st.permutations([100 * M, 110 * M, 121 * M, 7 * M]), drop=st.integers(0, 4))
def test_value_order_does_not_matter(kit, perm, drop):
    # oracle: requirements 12-13 — matching is order-free; the checks for a permutation of
    # the produced values equal those for the sorted values (drop=4 keeps every value).
    case = kit.case(kit.hx_trend())
    values = [v for i, v in enumerate(perm) if i != drop]
    base = grade_v2(case, kit.answer(values=sorted(values), fact_ids=[31]))
    got = grade_v2(case, kit.answer(values=values, fact_ids=[31]))
    assert got.grade is base.grade
    assert kit.checks(got) == kit.checks(base)
    present = {100 * M: "FY2023", 110 * M: "FY2024", 121 * M: "FY2025"}
    assert kit.checks(got) == {e: (v in values) for v, e in present.items()}
