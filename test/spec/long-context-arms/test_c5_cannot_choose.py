"""C5: the resolver cannot choose: a refusal in the denominator, never an exclusion."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

KEY = "AAA/2024/10-K/canonical.htm"


def _assert_refused(answer, adapter, status: str, expected_cost: float) -> None:
    assert answer.refused is True
    assert answer.error is None
    assert isinstance(answer.text, str) and answer.text
    assert answer.numeric_values == []
    assert adapter.calls == 0
    assert answer.selection["status"] == status
    assert answer.selection["reason"]
    # tolerance: float sum of a few rates
    assert answer.cost_usd == pytest.approx(expected_cost, rel=1e-9)


@pytest.mark.criterion("C5")
@pytest.mark.parametrize(
    "companies, periods",
    [
        pytest.param([], ["fiscal 2024"], id="no-company"),
        pytest.param(["Nobody Corp"], ["fiscal 2024"], id="ambiguous-company"),
        pytest.param(["AAA", "Nobody Corp"], ["fiscal 2024"], id="one-of-two-ambiguous"),
        pytest.param(["AAA"], ["sometime soon"], id="ambiguous-period"),
        pytest.param(["NUL"], [], id="no-period-and-no-last-seen-year"),
    ],
)
def test_undecided_is_a_refusal(
    make_resolved, make_case, router_reply, router_cost, companies, periods
):
    # oracle: requirement 2 — BranchDispatcher degrades on these and never guesses; "Nobody
    # Corp" matches no ticker or name in ENTITIES, and "sometime soon" names no year
    arm, _, adapter, resolver, _ = make_resolved(router_text=router_reply(companies, periods))
    answer = arm.answer(make_case())
    _assert_refused(answer, adapter, "undecided", router_cost())
    assert resolver.calls == []


@pytest.mark.criterion("C5")
def test_a_failed_entity_lookup_is_undecided(make_resolved, make_case, router_reply, router_cost):
    # oracle: resolve_companies turns a database failure into ambiguous, and XbrlRag degrades
    arm, _, adapter, _, _ = make_resolved(
        router_text=router_reply(["AAA"], ["fiscal 2024"]), db_error=ConnectionError("db down")
    )
    _assert_refused(arm.answer(make_case()), adapter, "undecided", router_cost())


@pytest.mark.criterion("C5")
def test_a_resolved_year_with_no_filing_is_a_refusal(
    make_resolved, make_case, router_reply, router_cost
):
    # oracle: requirement 8 — the year may be the resolver's error, so it must cost the arm
    arm, _, adapter, resolver, store = make_resolved(
        router_text=router_reply(["AAA"], ["fiscal 2021"]), keys={}
    )
    answer = arm.answer(make_case())
    _assert_refused(answer, adapter, "no_filing", router_cost())
    assert resolver.calls == [("AAA", 2021)]
    assert store.reads == []


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("ticker, fy", [(None, 2024), ("AAA", None), (None, None)])
def test_a_case_with_no_single_filing_is_excluded_before_the_router(
    make_resolved, make_case, ticker, fy
):
    # oracle: requirement 7 — the oracle arm's own exclusion, so both score the same cases
    arm, router_adapter, adapter, _, _ = make_resolved()
    answer = arm.answer(make_case(ticker=ticker, fy=fy))
    assert answer.error.startswith("excluded: ")
    assert router_adapter.calls == 0
    assert adapter.calls == 0
    # tolerance: no call was made, so the cost is exactly zero
    assert answer.cost_usd == pytest.approx(0.0, abs=0.0)


@pytest.mark.criterion("C5")
def test_a_key_not_in_the_store_is_excluded(make_resolved, make_case, doc):
    # oracle: requirement 8 — a fact about the corpus, as in the oracle arm
    arm, _, adapter, _, _ = make_resolved(objects={"other/key": doc("x")})
    answer = arm.answer(make_case())
    assert answer.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert answer.selection["status"] == "not_in_store"


@pytest.mark.criterion("C5")
def test_text_over_the_limit_is_excluded_not_truncated(make_resolved, make_case, doc):
    # oracle: requirement 8 — 4 * 10 = 40 characters is the limit; the text has 41
    arm, _, adapter, _, _ = make_resolved(objects={KEY: doc("x" * 41)}, max_input_tokens=10)
    answer = arm.answer(make_case())
    assert answer.error.startswith("excluded: ")
    assert adapter.calls == 0
    assert answer.selection["status"] == "over_window"


@pytest.mark.criterion("C5")
@given(
    companies=st.lists(st.sampled_from(["AAA", "BBB"]), max_size=2),
    unresolved_at=st.integers(0, 2),
)
def test_one_unresolved_mention_refuses_whatever_else_resolved(
    make_resolved, make_case, router_reply, companies, unresolved_at
):
    # oracle: requirement 2 — any ambiguous company is a reason, wherever it sits in the list
    mentions = list(companies)
    mentions.insert(min(unresolved_at, len(mentions)), "Nobody Corp")
    arm, _, adapter, resolver, _ = make_resolved(
        router_text=router_reply(mentions, ["fiscal 2024"])
    )
    answer = arm.answer(make_case())
    assert answer.refused is True
    assert answer.error is None
    assert adapter.calls == 0
    assert resolver.calls == []
