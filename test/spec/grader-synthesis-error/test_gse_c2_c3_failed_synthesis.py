"""C2 and C3: a failed synthesis grades alike for every kind (docs/specs/grader-synthesis-error.md,
requirement 2).

Each builder's answer is graded right when no fallback text is present (checked here), so only
the fallback decides the grade under test.
"""

from __future__ import annotations

import dataclasses

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.checkpoint.run import error_origin
from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case
from src.eval.core.system import SystemAnswer

KINDS = ["lookup", "ratio", "sum", "median", "screen.top", "peer_relative", "trend"]


def _assert_error(grade):
    assert grade.grade is Grade.ERROR
    assert grade.passed is False
    assert grade.matched_value is None
    assert grade.value_correct is False
    assert grade.confident_wrong is False
    assert grade.silent_wrong is False
    assert grade.error
    assert error_origin(grade.error) == "arm"


def _assert_refused(grade):
    assert grade.grade is Grade.REFUSED
    assert grade.passed is False
    assert grade.matched_value is None
    assert grade.value_correct is False
    assert grade.confident_wrong is False
    assert grade.silent_wrong is False


@pytest.mark.criterion("C2")
def test_the_meta_answer_is_an_error(kit):
    # oracle: requirement 2 — the stored META answer (hedged, values from the universe
    # sentence) carries E, so ERROR, not "ranked 33.0 (values)" INCORRECT
    case = kit.peer_case("DEVS", 3)
    ans = kit.answer(kit.META_TEXT, values=[33.0, 14.0], hedged=True)
    _assert_error(grade_case(case, ans))
    # a system error still wins: the existing answer.error rule is checked first
    raised = grade_case(case, SystemAnswer(text=kit.E, error="RuntimeError('boom')"))
    assert raised.grade is Grade.ERROR
    assert raised.error == "RuntimeError('boom')"
    assert "boom" in raised.reason


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_an_error_even_when_right(kit, kind):
    # oracle: requirement 2 — the builder's answer is CORRECT without the fallback (checked);
    # with E prefixed it is ERROR, never CORRECT
    case, ans = kit.BUILDERS[kind]()
    assert grade_case(case, ans).grade is Grade.CORRECT
    failed = dataclasses.replace(ans, text=f"{kit.E} {ans.text}")
    _assert_error(grade_case(case, failed))


@pytest.mark.criterion("C2")
@pytest.mark.parametrize("kind", KINDS)
def test_hedged_and_refused_flags_do_not_matter(kit, kind):
    # oracle: requirement 2 — whatever the flags, values and rows
    case, _ = kit.BUILDERS[kind]()
    for refused, hedged in ((False, True), (True, True), (False, False)):
        ans = kit.answer(kit.META_TEXT, values=[33.0, 14.0], refused=refused, hedged=hedged)
        _assert_error(grade_case(case, ans))


_ROWS = st.lists(st.tuples(st.sampled_from(["DEVA", "DEVB", "DEVS", "DEVC", "DEVD"]),
                           st.sampled_from([100e6, 200e6, 300e6, 400e6, 500e6])),
                 max_size=5)
_VALUES = st.lists(st.sampled_from([3.0, 14.0, 33.0, 0.25, 300e6, 500e6, 1500e6]), max_size=6)


def _failed(kit, kind, data, sentence):
    case, ans = kit.BUILDERS[kind]()
    keep = data.draw(st.booleans(), label="keep the right answer")
    prefix = data.draw(st.sampled_from(["", "[q1] Part one?\nDEVA was 1st.\n\n[q2] Two?\n"]),
                       label="prefix")
    tail = data.draw(st.sampled_from(["", " " + kit.disclosure_text()]), label="tail")
    if keep:
        text, values, rows = ans.text, list(ans.numeric_values), [
            (r["ticker"], r["value"]) for r in ans.rows]
    else:
        text, values, rows = "", data.draw(_VALUES, label="values"), data.draw(_ROWS)
    failed = kit.answer(f"{prefix}{sentence} {text}{tail}", values=values, rows=rows,
                        refused=data.draw(st.booleans(), label="refused"),
                        hedged=data.draw(st.booleans(), label="hedged"))
    failed.fact_ids = data.draw(st.sampled_from([[], [1], [1, 2]]), label="fact_ids")
    return case, failed


@pytest.mark.criterion("C2")
@given(kind=st.sampled_from(KINDS), data=st.data())
def test_any_answer_with_the_error_fallback_is_an_error(kit, kind, data):
    # oracle: requirement 2 — every kind, any values, rows, citations and flags
    case, failed = _failed(kit, kind, data, kit.E)
    _assert_error(grade_case(case, failed))


@pytest.mark.criterion("C3")
def test_the_refusal_fallback_is_refused(kit):
    # oracle: requirement 2 — R is the provider refusal: REFUSED, even beside values that
    # the universe sentence states
    case = kit.peer_case("DEVS", 3)
    ans = kit.answer(kit.META_TEXT.replace(kit.E, kit.R), values=[33.0, 14.0], hedged=True)
    _assert_refused(grade_case(case, ans))


@pytest.mark.criterion("C3")
@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_is_refused_even_when_right(kit, kind):
    # oracle: requirement 2 — never CORRECT, whatever the answer also says
    case, ans = kit.BUILDERS[kind]()
    _assert_refused(grade_case(case, dataclasses.replace(ans, text=f"{kit.R} {ans.text}")))


@pytest.mark.criterion("C3")
@given(kind=st.sampled_from(KINDS), data=st.data())
def test_any_answer_with_the_refusal_fallback_is_refused(kit, kind, data):
    # oracle: requirement 2 — every kind, any values, rows, citations and flags
    case, failed = _failed(kit, kind, data, kit.R)
    _assert_refused(grade_case(case, failed))
