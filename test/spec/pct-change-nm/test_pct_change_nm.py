"""Spec tests for pct-change-nm: a percentage change from a non-positive base is "not
meaningful" (docs/specs/pct-change-nm.md).

Calculator and grader are pure — no DB, no LLM.
"""

from __future__ import annotations

import keyword

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.core.graders.dispatch import grade_scalar
from src.eval.core.system import SystemAnswer
from src.retriever.xbrl.xbrl_calculator_tool import XbrlCalculatorTool

# Micron Technology operating income, from its FY2024 10-K: FY2023 -5,745,000,000;
# FY2024 1,304,000,000. Under the |base| convention the change is
# (1304 - (-5745)) / |-5745| = 7049 / 5745 = 1.22698...
MU_OP23 = -5_745_000_000.0
MU_OP24 = 1_304_000_000.0
MU_ABS_CHANGE = 7049 / 5745

# Percentage-change templates over a current value `a` and a base `b`.
PCT_FORMS = [
    "({a} - {b}) / {b}",
    "{a} / {b} - 1",
    "({a} - {b}) / {b} * 100",
    "(({a}-{b})/{b})",
    "(  ({a} - {b})  ) / ( {b} )",
    "(({a} - {b}) / {b}) * 100",
]
NOT_PCT_FORMS = ["{a} / {b}", "({a} - {b}) / c", "{a} - {b}", "({a} + {b}) / {b}"]

identifiers = st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True)

calc = XbrlCalculatorTool()


def _nm_case(**extra) -> EvalCase:
    return EvalCase.from_dict(
        {
            "id": "aq-2c-yoy-growth:concept=OperatingIncome-fy=2024-ticker=MU",
            "question": "By what percentage did Micron's operating income change in "
            "fiscal 2024 versus the prior year?",
            "kind": "compute",
            "expected_value": MU_ABS_CHANGE,
            "rel_tol": 0.01,
            "expect_not_meaningful": True,
            **extra,
        }
    )


def _answer(values, *, hedged=False, refused=False) -> SystemAnswer:
    return SystemAnswer(
        text="synthetic answer",
        numeric_values=list(values),
        fact_ids=[1, 2] if values else [],
        hedged=hedged,
        refused=refused,
    )


# --------------------------------------------------------------------------- C1
@pytest.mark.criterion("C1")
def test_recognizes_percentage_change_forms():
    # oracle: definition of percentage change, (new - base) / base, optionally x100 or
    # written as new / base - 1
    for form in PCT_FORMS:
        assert XbrlCalculatorTool.pct_change_base(form.format(a="op24", b="op23")) == "op23", form
    for form in NOT_PCT_FORMS:
        assert XbrlCalculatorTool.pct_change_base(form.format(a="op24", b="op23")) is None, form


@pytest.mark.criterion("C1")
@given(st.sampled_from(PCT_FORMS), identifiers, identifiers)
def test_any_pct_form_over_any_step_ids_returns_the_base(form, a, b):
    # oracle: property — the base is the step the difference is divided by, whatever
    # the step ids are called
    assume(a != b and "c" not in (a, b))
    assume(not keyword.iskeyword(a) and not keyword.iskeyword(b))  # step ids are identifiers
    assert XbrlCalculatorTool.pct_change_base(form.format(a=a, b=b)) == b
    assert XbrlCalculatorTool.pct_change_base(f"{a} / {b}") is None


# --------------------------------------------------------------------------- C2
@pytest.mark.criterion("C2")
def test_micron_negative_base_is_not_meaningful():
    # oracle: Micron FY2023 -> FY2024 operating income (10-K); a percentage change from a
    # negative base is reported "n/m"
    expr = "(op24 - op23) / op23"
    reason = calc.not_meaningful_reason(expr, {"op24": MU_OP24, "op23": MU_OP23})
    assert isinstance(reason, str) and reason.strip()
    assert calc.not_meaningful_reason(expr, {"op24": MU_OP24, "op23": 5_745_000_000.0}) is None
    assert calc.not_meaningful_reason("op24 / op23", {"op24": MU_OP24, "op23": MU_OP23}) is None


@pytest.mark.criterion("C2")
@given(
    st.sampled_from(PCT_FORMS),
    st.floats(min_value=-1e12, max_value=1e12, allow_nan=False),
    st.floats(min_value=-1e12, max_value=0.0, allow_nan=False),
)
def test_any_non_positive_base_has_a_reason(form, current, base):
    # oracle: property — the n/m rule applies to every percentage-change form and every
    # base <= 0 (zero included), whatever the current value
    expr = form.format(a="cur", b="base")
    reason = calc.not_meaningful_reason(expr, {"cur": current, "base": base})
    assert isinstance(reason, str) and reason.strip()


# --------------------------------------------------------------------------- C3
@pytest.mark.criterion("C3")
def test_positive_base_evaluates_unchanged():
    # oracle: closed form (a - b) / b computed in the test
    a, b = 193_737_000_000.0, 115_186_000_000.0
    expr = "(dc26 - dc25) / dc25"
    assert XbrlCalculatorTool.pct_change_base(expr) == "dc25"
    assert calc.not_meaningful_reason(expr, {"dc26": a, "dc25": b}) is None
    # tolerance: one float division; rel=1e-12 absorbs only rounding
    assert calc.evaluate(expr, {"dc26": a, "dc25": b}) == pytest.approx((a - b) / b, rel=1e-12)


@pytest.mark.criterion("C3")
@given(
    st.floats(min_value=-1e12, max_value=1e12, allow_nan=False),
    st.floats(min_value=1.0, max_value=1e12, allow_nan=False),
)
def test_any_positive_base_keeps_the_closed_form(current, base):
    # oracle: property — for a base > 0 nothing changes: no reason, and evaluate returns
    # (a - b) / b
    expr = "(cur - base) / base"
    assert XbrlCalculatorTool.pct_change_base(expr) == "base"
    assert calc.not_meaningful_reason(expr, {"cur": current, "base": base}) is None
    # tolerance: one float subtraction and division; rel=1e-9, abs=1e-12 near zero
    assert calc.evaluate(expr, {"cur": current, "base": base}) == pytest.approx(
        (current - base) / base, rel=1e-9, abs=1e-12
    )


# --------------------------------------------------------------------------- C4
@pytest.mark.criterion("C4")
def test_scaling_and_sign_flip_point_case():
    # oracle: a ratio is scale-invariant; flipping the base below zero triggers n/m
    expr = "(cur - base) / base"
    small = {"cur": 120.0, "base": 100.0}
    big = {"cur": 120_000.0, "base": 100_000.0}
    assert calc.not_meaningful_reason(expr, small) is None
    assert calc.not_meaningful_reason(expr, big) is None
    # tolerance: one float division each; rel=1e-12 absorbs rounding
    assert calc.evaluate(expr, big) == pytest.approx(calc.evaluate(expr, small), rel=1e-12)
    flipped = calc.not_meaningful_reason(expr, {"cur": 120.0, "base": -100.0})
    assert isinstance(flipped, str) and flipped.strip()


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
@given(
    st.floats(min_value=-1e9, max_value=1e9, allow_nan=False),
    st.floats(min_value=1e-3, max_value=1e9, allow_nan=False),
    st.floats(min_value=1e-3, max_value=1e3, allow_nan=False),
)
def test_scaling_preserves_and_sign_flip_triggers_the_reason(current, base, k):
    # oracle: metamorphic — scaling both values by k > 0 changes neither whether the
    # change is meaningful nor its value; negating a positive base makes it n/m
    expr = "(cur - base) / base"
    plain = {"cur": current, "base": base}
    scaled = {"cur": current * k, "base": base * k}
    assert bool(calc.not_meaningful_reason(expr, plain)) == bool(
        calc.not_meaningful_reason(expr, scaled)
    )
    # tolerance: scaling adds a few float roundings; rel=1e-9, abs=1e-9 near zero
    assert calc.evaluate(expr, scaled) == pytest.approx(
        calc.evaluate(expr, plain), rel=1e-9, abs=1e-9
    )
    flipped = calc.not_meaningful_reason(expr, {"cur": current, "base": -base})
    assert isinstance(flipped, str) and flipped.strip()


# --------------------------------------------------------------------------- C5
@pytest.mark.criterion("C5")
def test_grading_an_n_m_case():
    # oracle: requirement — "n/m" is the right answer: declining (with or without the two
    # components) is correct, stating the ratio is wrong, asserting any value is wrong
    case = _nm_case()

    shown_components = grade_scalar(case, _answer([MU_OP23, MU_OP24], hedged=True))
    assert shown_components.grade.value == "CORRECT" and shown_components.passed is True

    assert grade_scalar(case, _answer([], refused=True)).grade.value == "CORRECT"
    assert grade_scalar(case, _answer([], hedged=True)).grade.value == "CORRECT"

    hedged_ratio = grade_scalar(case, _answer([MU_OP23, MU_OP24, -MU_ABS_CHANGE], hedged=True))
    assert hedged_ratio.grade.value == "INCORRECT"
    assert hedged_ratio.silent_wrong is False

    asserted_ratio = grade_scalar(case, _answer([-MU_ABS_CHANGE * 100]))
    assert asserted_ratio.grade.value == "INCORRECT"
    assert asserted_ratio.silent_wrong is True

    asserted_components = grade_scalar(case, _answer([MU_OP23, MU_OP24]))
    assert asserted_components.grade.value == "INCORRECT"

    # without the flag the case grades exactly as before this spec
    plain = _nm_case(expect_not_meaningful=False)
    shown_plain = grade_scalar(plain, _answer([MU_OP23, MU_OP24], hedged=True))
    assert shown_plain.grade.value == "HEDGED_MISS"


@pytest.mark.criterion("C5")
@given(
    st.floats(min_value=0.05, max_value=20.0, allow_nan=False),
    st.lists(st.floats(min_value=-1e12, max_value=1e12, allow_nan=False), max_size=4),
    st.sampled_from([1.0, -1.0, 100.0, -100.0]),
)
def test_hedged_answers_pass_unless_they_state_the_ratio(v, components, scale):
    # oracle: property — a hedged n/m answer is correct whatever component values it
    # shows, as long as none of them is the ratio (±v or ±100v); adding the ratio flips it
    case = _nm_case(expected_value=v)
    claims = [c * v for c in (1.0, -1.0, 100.0, -100.0)]
    assume(all(abs(x - c) > 0.02 * abs(c) for x in components for c in claims))

    assert grade_scalar(case, _answer(components, hedged=True)).grade.value == "CORRECT"
    with_ratio = grade_scalar(case, _answer([*components, scale * v], hedged=True))
    assert with_ratio.grade.value == "INCORRECT"
