"""C3: exclusions, not truncation — an excluded case makes no LLM call."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.eval.core.case import EvalCase
from src.eval.systems.long_context import filing_text

KEY = "AAA/2024/10-K/canonical.htm"


def _case(ticker: str | None = "AAA", fy: int | None = 2024) -> EvalCase:
    return EvalCase(
        id="c3", question="What was AAA total revenue in fiscal 2024?", kind="lookup",
        ticker=ticker, fy=fy,
    )


def _assert_excluded(ans, adapter) -> None:
    assert isinstance(ans.error, str)
    assert ans.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert ans.numeric_values == []
    # tolerance: no call was made, so the cost is exactly zero
    assert ans.cost_usd == pytest.approx(0.0, abs=0.0)


@pytest.mark.criterion("C3")
def test_no_ticker_is_excluded_without_resolving(make_arm):
    # oracle: requirement 6 — a case without a ticker has no single document
    arm, adapter, resolver, _ = make_arm()
    _assert_excluded(arm.answer(_case(ticker=None)), adapter)
    assert resolver.calls == []


@pytest.mark.criterion("C3")
def test_no_fiscal_year_is_excluded_without_resolving(make_arm):
    # oracle: requirement 6 — a case without a fiscal year has no single document
    arm, adapter, resolver, _ = make_arm()
    _assert_excluded(arm.answer(_case(fy=None)), adapter)
    assert resolver.calls == []


@pytest.mark.criterion("C3")
def test_unresolved_filing_is_excluded(make_arm):
    # oracle: requirement 6 — the resolver returns None
    arm, adapter, resolver, store = make_arm(keys={})
    _assert_excluded(arm.answer(_case()), adapter)
    assert resolver.calls == [("AAA", 2024)]
    assert store.reads == []


@pytest.mark.criterion("C3")
def test_key_missing_from_store_is_excluded(make_arm, xhtml):
    # oracle: requirement 6 — the resolved key has no object in the store
    arm, adapter, resolver, _ = make_arm(
        objects={"AAA/2023/10-K/canonical.htm": xhtml("<p>other year</p>")}
    )
    _assert_excluded(arm.answer(_case()), adapter)
    assert resolver.calls == [("AAA", 2024)]


@pytest.mark.criterion("C3")
def test_text_over_the_limit_is_excluded(make_arm, xhtml):
    # oracle: requirement 6 — max_input_tokens=10 allows 40 characters; a single
    # paragraph of 41 letters is 41 characters of text (C1 rules)
    arm, adapter, _, _ = make_arm(xhtml("<p>" + "a" * 41 + "</p>"), max_input_tokens=10)
    _assert_excluded(arm.answer(_case()), adapter)


@pytest.mark.criterion("C3")
def test_default_limit_is_250k_tokens(make_arm, xhtml):
    # oracle: requirement 6 — the default max_input_tokens is 250_000, so 1,000,000
    # characters of text are sent and 1,000,001 are excluded
    at_limit, adapter_at, _, _ = make_arm(xhtml("<p>" + "a" * 1_000_000 + "</p>"))
    ans = at_limit.answer(_case())
    assert ans.error is None
    assert adapter_at.calls == 1

    over, adapter_over, _, _ = make_arm(xhtml("<p>" + "a" * 1_000_001 + "</p>"))
    _assert_excluded(over.answer(_case()), adapter_over)


@pytest.mark.criterion("C3")
@given(n=st.integers(min_value=1, max_value=60), extra=st.sampled_from([0, 1]))
def test_boundary_is_exact(make_arm, xhtml, n, extra):
    # oracle: requirement 6 — text of exactly 4n characters is sent, 4n + 1 is excluded;
    # a single paragraph of k letters is k characters (C1 rules)
    html = xhtml("<p>" + "b" * (4 * n + extra) + "</p>")
    assert len(filing_text(html)) == 4 * n + extra
    arm, adapter, _, _ = make_arm(html, max_input_tokens=n)
    ans = arm.answer(_case())
    if extra == 0:
        assert ans.error is None
        assert adapter.calls == 1
    else:
        _assert_excluded(ans, adapter)


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(
    reason=st.sampled_from(["no_ticker", "no_fy", "unresolved", "missing", "too_long"]),
    fy=st.integers(min_value=2000, max_value=2030),
)
def test_every_exclusion_makes_no_call(make_arm, xhtml, reason, fy):
    # oracle: requirement 6 — whichever exclusion holds, the answer is an "excluded: "
    # error with no call, no numbers and no cost; the same arm with the condition
    # removed does make the call
    key = f"AAA/{fy}/10-K/canonical.htm"
    short = xhtml("<p>Revenue was 7 dollars</p>")
    keys = {("AAA", fy): key}
    objects = {key: short}
    case = _case(fy=fy)
    if reason == "no_ticker":
        case = _case(ticker=None, fy=fy)
    elif reason == "no_fy":
        case = _case(fy=None)
    elif reason == "unresolved":
        keys = {}
    elif reason == "missing":
        objects = {}
    else:
        objects = {key: xhtml("<p>" + "c" * 401 + "</p>")}
    arm, adapter, _, _ = make_arm(keys=keys, objects=objects, max_input_tokens=100)
    _assert_excluded(arm.answer(case), adapter)

    control, control_adapter, _, _ = make_arm(
        keys={("AAA", fy): key}, objects={key: short}, max_input_tokens=100
    )
    assert control.answer(_case(fy=fy)).error is None
    assert control_adapter.calls == 1
