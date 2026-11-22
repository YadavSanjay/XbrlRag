"""C2: which grader grades a case; legacy cases grade exactly as today.

Oracle: requirements 2-4 of docs/specs/v2-grading.md. The regression property's oracle is
today's dispatch, written out here from the four legacy graders by kind.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import (
    grade_breakdown,
    grade_case,
    grade_narrative,
    grade_scalar,
    grade_screen,
)
from src.eval.core.graders.v2 import grade_v2, v2_subkind
from src.eval.core.system import SystemAnswer

HX_TABLE = [
    ("cross_filer", "screen_top", "screen"), ("cross_filer", "screen_bottom", "screen"),
    ("cross_filer", "count", "count"), ("cross_filer", "sum", "sum"),
    ("cross_filer", "median", "median"), ("cross_filer", "peer_relative", "rank"),
    ("cross_period", "trend", "trend"), ("cross_period", "cagr", "cagr"),
    ("cross_period", "point_in_time", "point_in_time"), ("compound", None, "compound"),
    ("honesty", "ambiguous", "ambiguous"), ("honesty", "false_premise", "false_premise"),
    ("honesty", "decline", "decline"), ("text", None, "text"),
]
V2_TABLE = [
    ("screen.top", "screen"), ("screen.bottom", "screen"), ("screen.sum", "sum"),
    ("screen.median", "median"), ("peer_relative", "peer_relative"), ("trend", "trend"),
    ("compute.cagr", "cagr"), ("compute.ratio", "ratio"), ("lookup", None),
    ("point_in_time", None), ("compute.yoy_growth", None), ("compute.difference", None),
]


def _legacy(case: EvalCase, answer: SystemAnswer):
    """Today's dispatch (requirement 3's 'exactly as today')."""
    if case.kind == "screen":
        return grade_screen(case, answer)
    if case.kind == "breakdown":
        return grade_breakdown(case, answer)
    if case.kind == "narrative":
        return grade_narrative(case, answer)
    return grade_scalar(case, answer)


@pytest.mark.criterion("C2")
def test_subkind_table(kit):
    # oracle: requirement 2, the table written out above by hand.
    for kind, subtype, want in HX_TABLE:
        tags = ["split:external", kind] + ([subtype] if subtype else [])
        case = EvalCase(id="x", question="q?", kind="lookup", tags=tags)
        assert v2_subkind(case) == want, (kind, subtype)
    for shape, want in V2_TABLE:
        case = kit.case(kit.v2_with_shape(shape))
        assert v2_subkind(case) == want, shape
    legacy = EvalCase(id="y", question="q?", kind="screen", tags=["split:dev", "growth"])
    assert v2_subkind(legacy) is None


@pytest.mark.criterion("C2")
def test_grade_v2_refuses_a_legacy_case(kit):
    # oracle: requirement 3 — grade_v2 of a case with no sub-kind raises ValueError.
    with pytest.raises(ValueError):
        grade_v2(kit.case(kit.v2_lookup()), kit.answer(values=[121 * kit.BN], fact_ids=[1]))
    assert v2_subkind(kit.case(kit.hx_text())) == "text"


@pytest.mark.criterion("C2")
def test_text_case_routes_to_the_narrative_path(kit):
    # oracle: requirement 18 — a text case grades as grade_narrative with checks None.
    case = kit.case(kit.hx_text())
    ans = kit.answer(text="A synthetic narrative answer.")
    got = grade_case(case, ans)
    want = grade_narrative(case, ans)
    assert got.to_dict() == want.to_dict()
    assert kit.checks(got) is None
    assert v2_subkind(case) == "text"


@pytest.mark.criterion("C2")
def test_errored_answer_is_error_without_checks(kit):
    # oracle: requirement 3 — an answer with error is ERROR as today, checks None, even for
    # a v2 case.
    case = kit.case(kit.hx_trend())
    got = grade_case(case, kit.answer(error="boom"))
    assert got.grade is Grade.ERROR
    assert got.passed is False
    assert kit.checks(got) is None
    assert v2_subkind(case) == "trend"


@pytest.mark.criterion("C2")
def test_v2_case_is_graded_by_its_v2_grader(kit):
    # oracle: requirements 3 and 12 — a trend with every year present passes, where it is
    # a v2 case; grade_case equals grade_v2 and records checks.
    case = kit.case(kit.hx_trend())
    ans = kit.answer(values=[100 * kit.M, 110 * kit.M, 121 * kit.M], fact_ids=[31, 32, 33])
    got = grade_case(case, ans)
    assert got.to_dict() == grade_v2(case, ans).to_dict()
    assert kit.checks(got) == {"FY2023": True, "FY2024": True, "FY2025": True}


_kinds = st.sampled_from(["lookup", "compute", "dimensional", "screen", "breakdown",
                          "narrative"])
_tags = st.lists(st.sampled_from(["split:dev", "growth", "family:plain", "shape:lookup",
                                  "shape:compute.difference", "shape:point_in_time",
                                  "split:external"]), max_size=3)
_vals = st.lists(st.sampled_from([1.0, 2.0, 100.0, 121e9, -5.0]), max_size=3)


@pytest.mark.criterion("C2")
@given(kind=_kinds, tags=_tags, expected=st.sampled_from([None, 2.0, 121e9]),
       values=_vals, fact_ids=st.lists(st.integers(1, 4), max_size=2),
       refused=st.booleans(), hedged=st.booleans(), text=st.sampled_from(["", "DEVA 4"]))
def test_legacy_cases_grade_exactly_as_today(kind, tags, expected, values, fact_ids,
                                             refused, hedged, text):
    # oracle: requirement 3 — today's dispatch (`_legacy`), unchanged; checks None and no
    # "checks" key in to_dict.
    rows = [{"entity": "DEVA", "value": 2.0}] if kind in ("screen", "breakdown") else None
    case = EvalCase(id="r", question="q?", kind=kind, tags=tags, expected_value=expected,
                    expected_rows=rows, expected_fact_ids=[1])
    ans = SystemAnswer(text=text, numeric_values=values,
                       rows=[{"ticker": "DEVA", "value": v} for v in values],
                       fact_ids=fact_ids, refused=refused, hedged=hedged)
    got = grade_case(case, ans)
    assert got.to_dict() == _legacy(case, ans).to_dict()
    assert getattr(got, "checks", "<no checks attribute>") is None


@pytest.mark.criterion("C2")
@pytest.mark.metamorphic
def test_grade_case_equals_grade_v2_for_every_v2_case(kit):
    # oracle: requirement 3 — for every sub-kind, grade_case and grade_v2 agree.
    cases = [kit.hx_screen(), kit.hx_count(), kit.hx_sum(), kit.hx_median(), kit.hx_rank(),
             kit.hx_trend(), kit.hx_cagr(), kit.hx_pit(), kit.hx_compound(),
             kit.hx_ambiguous(), kit.hx_false_premise(), kit.hx_decline(), kit.v2_screen(),
             kit.v2_peer(), kit.v2_ratio()]
    ans = kit.answer(text="Among the 4 companies DEVA leads; DEVD is excluded.",
                     values=[2.0, 300 * kit.M], rows=[{"ticker": "DEVA", "value": 300 * kit.M}],
                     fact_ids=[1])
    for d in cases:
        case = kit.case(d)
        got = grade_case(case, ans)
        assert got.to_dict() == grade_v2(case, ans).to_dict(), d["id"]
        assert isinstance(kit.checks(got), dict), d["id"]
