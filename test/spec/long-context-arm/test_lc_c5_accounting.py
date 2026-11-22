"""C5: accounting per answer; boundary errors are contained, a run halt propagates."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.llm.run_budget import RunBudget, RunHalt


class ProviderRejectedError(Exception):
    """A non-transient provider error (its name is not in the transient list)."""


def _case(case_id: str = "c5") -> EvalCase:
    return EvalCase(
        id=case_id, question="What was AAA total revenue in fiscal 2024?", kind="lookup",
        ticker="AAA", fy=2024,
    )


def _assert_real_error(ans) -> None:
    assert isinstance(ans.error, str) and ans.error != ""
    assert not ans.error.startswith("excluded: ")
    assert ans.numeric_values == []


@pytest.mark.criterion("C5")
def test_answers_carry_own_trace_cost_and_latency(make_arm):
    # oracle: requirement 7 — a provider call was made, so cost is positive, and each
    # answer opens its own trace
    arm, adapter, _, _ = make_arm()
    first = arm.answer(_case("a"))
    second = arm.answer(_case("b"))
    assert adapter.calls == 2
    for ans in (first, second):
        assert ans.error is None
        assert isinstance(ans.query_id, str) and ans.query_id != ""
        assert ans.cost_usd > 0
        assert ans.latency_ms >= 0
    assert first.query_id != second.query_id
    # tolerance: identical tokens at one price give the same float; 1e-9 guards summation
    assert second.cost_usd == pytest.approx(first.cost_usd, rel=1e-9)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(
    prompt_tokens=st.integers(min_value=1, max_value=136_000),
    completion_tokens=st.integers(min_value=1, max_value=50_000),
)
def test_doubling_tokens_doubles_cost(make_arm, prompt_tokens, completion_tokens):
    # oracle: below the 272K tier pricing is linear and the fake reports no cached tokens, so
    # doubling prompt and completion tokens doubles cost_usd
    base_arm, _, _, _ = make_arm(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    double_arm, _, _, _ = make_arm(
        prompt_tokens=2 * prompt_tokens, completion_tokens=2 * completion_tokens
    )
    base = base_arm.answer(_case())
    double = double_arm.answer(_case())
    assert base.error is None and double.error is None
    assert base.cost_usd > 0
    # tolerance: linear pricing in float arithmetic; 1e-9 relative covers rounding
    assert double.cost_usd == pytest.approx(2 * base.cost_usd, rel=1e-9)
    assert base.latency_ms >= 0 and double.latency_ms >= 0


@pytest.mark.criterion("C5")
def test_resolver_error_is_a_real_error(make_arm):
    # oracle: requirement 7 — a resolver exception is reported, not raised, not excluded
    arm, adapter, resolver, _ = make_arm(resolver_error=ConnectionRefusedError("db down"))
    ans = arm.answer(_case())
    assert resolver.calls == [("AAA", 2024)]
    assert adapter.calls == 0
    _assert_real_error(ans)


@pytest.mark.criterion("C5")
def test_store_error_is_a_real_error(make_arm):
    # oracle: requirement 7 — a store exception (not a missing key) is a real error
    arm, adapter, _, _ = make_arm(store_error=RuntimeError("s3 throttled"))
    ans = arm.answer(_case())
    assert adapter.calls == 0
    _assert_real_error(ans)


@pytest.mark.criterion("C5")
def test_provider_error_is_a_real_error(make_arm):
    # oracle: requirement 7 — a permanent provider exception is reported, not raised
    arm, adapter, _, _ = make_arm(provider_error=ProviderRejectedError("400 bad request"))
    ans = arm.answer(_case())
    assert adapter.calls == 1
    _assert_real_error(ans)


@pytest.mark.criterion("C5")
def test_zero_call_budget_raises_run_halt(make_arm):
    # oracle: requirement 7 — RunBudget(max_llm_calls=0) trips before the first call, and
    # RunHalt is the one exception that leaves answer()
    arm, adapter, _, _ = make_arm(run_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        arm.answer(_case())
    assert adapter.calls == 0


# FileNotFoundError is left out on purpose: it is the store's "missing key" signal (C3).
_ERRORS = st.sampled_from(
    [ValueError, RuntimeError, TypeError, ProviderRejectedError, ConnectionRefusedError,
     TimeoutError]
)
_HALT_BUDGETS = st.sampled_from(
    [
        RunBudget(max_llm_calls=0),
        RunBudget(max_cost_usd=0.0),
        RunBudget(max_tokens=0),
        RunBudget(max_consecutive_failures=0),
    ]
)


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(
    boundary=st.sampled_from(["resolver", "store", "provider"]),
    exc_type=_ERRORS,
    message=st.text(alphabet="abcdefghij -", min_size=1, max_size=20),
)
def test_any_boundary_error_is_contained(make_arm, boundary, exc_type, message):
    # oracle: requirement 7 — whichever boundary fails and however, answer() returns a
    # non-excluded error answer with no numbers
    err = exc_type(message)
    arm, _, _, _ = make_arm(
        resolver_error=err if boundary == "resolver" else None,
        store_error=err if boundary == "store" else None,
        provider_error=err if boundary == "provider" else None,
    )
    _assert_real_error(arm.answer(_case()))


@pytest.mark.criterion("C5")
@pytest.mark.metamorphic
@given(budget=_HALT_BUDGETS)
def test_any_tripped_budget_propagates(make_arm, budget):
    # oracle: requirement 7 — every RunBudget threshold at zero trips before the first
    # call (a RunHalt), and it propagates out of answer()
    arm, adapter, _, _ = make_arm(run_budget=budget)
    with pytest.raises(RunHalt):
        arm.answer(_case())
    assert adapter.calls == 0
