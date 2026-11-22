"""C5: Rule B in grading — a growth key asked as a "change" without a percent cue also accepts
the absolute change (docs/specs/pick-skips-questions.md, requirement 5).

Oracle: DEVA's capex legs FY2023 10,959,000,000 and FY2024 9,447,000,000 (decimals -6): the
absolute change is -1,512,000,000 and the growth -13.8% (both by hand); answers hand-written.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.graders.base import Grade
from src.eval.core.graders.dispatch import grade_case

CHANGE = -1_512_000_000.0
STATED_CHANGE = ("Deva Corp's capital expenditures were 10,959,000,000 USD in FY2023 and "
                 "9,447,000,000 USD in FY2024, a change of -1,512,000,000 USD.")
STATED_GROWTH = "Deva Corp's capital expenditures fell 13.8% from FY2023 to FY2024."


def _checks(grade):
    return getattr(grade, "checks", None) or {}


def _dependent_ok(grade) -> bool:
    return _checks(grade).get("dependent.value") is True and _checks(grade).get("dependent") is True


@pytest.mark.criterion("C5")
def test_compound_change_question_accepts_the_absolute_change(kit):
    # oracle: requirement 5(b) — "by how much did ... change" with no cue: the stated
    # -1,512,000,000 is the key's absolute change, so the dependent passes, by that reading
    g = grade_case(kit.lg_case(), kit.answer(STATED_CHANGE, [10_959_000_000.0, 9_447_000_000.0,
                                                               CHANGE]))
    assert _dependent_ok(g), g.reason
    assert g.grade is Grade.CORRECT, g.reason
    assert "absolute change" in g.reason


@pytest.mark.criterion("C5")
def test_a_percent_cue_or_a_wrong_change_still_fails(kit):
    # oracle: requirement 5 — with "growth" in the question only the rate is accepted; a wrong
    # change (-1,400,000,000) never matches; the change question accepts the right one
    values = [10_959_000_000.0, 9_447_000_000.0, CHANGE]
    right = grade_case(kit.lg_case(), kit.answer(STATED_CHANGE, values))
    assert _dependent_ok(right), right.reason
    cued = grade_case(kit.lg_case(kit.LG_GROWTH_Q), kit.answer(STATED_CHANGE, values))
    assert _checks(cued).get("dependent.value") is False, cued.reason
    assert cued.grade is not Grade.CORRECT
    wrong_text = STATED_CHANGE.replace("-1,512,000,000", "-1,400,000,000")
    wrong = grade_case(kit.lg_case(), kit.answer(wrong_text, [10_959_000_000.0,
                                                              9_447_000_000.0, -1.4e9]))
    assert _checks(wrong).get("dependent.value") is False, wrong.reason


@pytest.mark.criterion("C5")
def test_the_growth_form_passes_under_either_question(kit):
    # oracle: requirement 5 — the key is tried first and wins; -13.8% is the key's percent form
    # (the absolute change answer is the control that fails today)
    for q in (kit.LG_CHANGE_Q, kit.LG_GROWTH_Q):
        g = grade_case(kit.lg_case(q), kit.answer(STATED_GROWTH, [-13.8]))
        assert _dependent_ok(g), (q, g.reason)
    g = grade_case(kit.lg_case(), kit.answer(STATED_CHANGE, [CHANGE]))
    assert _dependent_ok(g), g.reason


@pytest.mark.criterion("C5")
def test_non_compound_growth_case(kit):
    # oracle: requirement 5(a) — the same reading for a compute.yoy_growth case; "By what
    # percentage" carries a cue, so there the change is not accepted
    ans = kit.answer("Deva Corp's capital expenditures changed by -1,512,000,000 USD.", [CHANGE])
    g = grade_case(kit.yoy_case(), ans)
    assert g.grade is Grade.CORRECT and g.passed is True, g.reason
    assert "absolute change" in g.reason
    cued = grade_case(kit.yoy_case(kit.YOY_PERCENT_Q), ans)
    assert cued.grade is not Grade.CORRECT and cued.passed is False, cued.reason


_QUESTIONS = (
    ("By how much did Deva Corp's capital expenditures change from fiscal 2022 to fiscal 2023?",
     True),
    ("What was the year-over-year change in Deva Corp's capital expenditures for fiscal 2023?",
     True),
    ("By what percentage did Deva Corp's capital expenditures change from fiscal 2022?", False),
    ("What was the growth rate of Deva Corp's capital expenditures in fiscal 2023?", False),
    ("Deva Corp capital expenditures change, fiscal 2022 to fiscal 2023, %", False),
)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(a=st.integers(1, 90_000), b=st.integers(1, 90_000), which=st.sampled_from(_QUESTIONS),
       compound=st.booleans())
def test_change_accepted_exactly_without_a_cue(kit, a, b, which, compound):
    # oracle: requirement 5 — legs on the reported step (millions, decimals -6); the stated
    # absolute change (b - a, in USD) is value-correct exactly when the question has the change
    # word and no cue; the growth in percent form is value-correct under any question
    question, offered = which
    lo, hi = a * 1_000_000, b * 1_000_000
    growth = (hi - lo) / lo
    legs = [kit.leg(lo, 2022), kit.leg(hi, 2023)]
    build = kit.lg_case if compound else kit.yoy_case
    case = build(question, legs=legs, growth=growth)
    change = float(hi - lo)
    by_change = grade_case(case, kit.answer(f"It changed by {change:,.0f} USD.", [change]))
    by_rate = grade_case(case, kit.answer(f"It changed by {growth * 100!r}%.", [growth * 100]))
    if compound:
        change_ok = _checks(by_change).get("dependent.value") is True
        rate_ok = _checks(by_rate).get("dependent.value") is True
    else:
        change_ok, rate_ok = by_change.value_correct, by_rate.value_correct
    if hi != lo:  # a zero change is also a zero rate
        assert change_ok is offered, (question, change, by_change.reason)
    assert rate_ok is True, (question, growth, by_rate.reason)
