"""C3: long_context_resolved chooses from the question, and only the filing differs."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prompts.prompt_registry import get_xbrl_registry
from src.eval.systems.long_context import filing_text
from src.llm.gateway import resolve_model_ref

A24 = "AAA/2024/10-K/canonical.htm"
A23 = "AAA/2023/10-K/canonical.htm"
B24 = "BBB/2024/10-K/canonical.htm"


@pytest.mark.criterion("C3")
def test_routes_the_question_and_sends_the_whole_filing(
    make_resolved, make_case, doc, router_reply
):
    # oracle: requirements 1 and 5, a hand-built filing and router reply
    html = doc("Alpha Holdings annual report.", "Total revenue was 7 dollars.")
    arm, router_adapter, adapter, resolver, store = make_resolved(
        router_text=router_reply(["AAA"], ["fiscal 2024"]), objects={A24: html}
    )
    case = make_case("What was revenue at Zqalpha in fiscal 2024?")
    answer = arm.answer(case)

    assert answer.error is None
    assert router_adapter.calls == 1
    assert router_adapter.stages == ["intent_router"]
    assert case.question in router_adapter.user_text()
    assert resolver.calls == [("AAA", 2024)]
    assert store.reads == [A24]
    assert adapter.calls == 1
    assert json.dumps(filing_text(html), ensure_ascii=False) in adapter.user_text()
    assert case.question in adapter.user_text()
    assert answer.numeric_values == [7.0]
    assert answer.fact_ids == []
    assert answer.chunk_ids == []


@pytest.mark.criterion("C3")
def test_same_stage_prompt_and_layout_as_the_oracle_arm(make_resolved, make_case):
    # oracle: requirement 5 — stage long_context, the oracle's system prompt, three messages
    # with the filing one the cache point
    arm, _, adapter, _, _ = make_resolved()
    arm.answer(make_case())
    assert adapter.stages == ["long_context"]
    params = adapter.params[0]
    ref = resolve_model_ref("gpt-5.6-sol")
    resolved = get_xbrl_registry().resolve("long_context", provider=ref.provider, series=ref.series)
    assert adapter.system_text() == resolved.system.format()
    assert params["model"] == "gpt-5.6-sol"
    roles = [m["role"] for m in adapter.messages()]
    assert roles == ["system", "user", "user"]
    assert adapter.messages()[1].get("cache_point") is True
    assert not adapter.messages()[2].get("cache_point")


@pytest.mark.criterion("C3")
def test_several_years_take_the_latest(make_resolved, make_case, doc, router_reply):
    # oracle: requirement 4 — the later filing carries the earlier year as comparatives
    arm, _, adapter, resolver, _ = make_resolved(
        router_text=router_reply(["AAA"], ["fiscal 2023", "fiscal 2024"]),
        keys={("AAA", 2023): A23, ("AAA", 2024): A24},
        objects={A23: doc("Zqearlier filing"), A24: doc("Zqlater filing")},
    )
    answer = arm.answer(make_case("How did AAA revenue change from fiscal 2023 to 2024?"))
    assert answer.error is None
    assert resolver.calls == [("AAA", 2024)]
    assert "Zqlater filing" in adapter.user_text()
    assert "Zqearlier filing" not in adapter.user_text()


@pytest.mark.criterion("C3")
def test_two_filers_cannot_be_one_filing(make_resolved, make_case, doc, router_reply):
    # oracle: requirement 4 — more than one company is a reason as in requirement 2
    arm, _, adapter, resolver, _ = make_resolved(
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]),
        keys={("AAA", 2024): A24, ("BBB", 2024): B24},
        objects={A24: doc("a"), B24: doc("b")},
    )
    answer = arm.answer(make_case("Which had more revenue, AAA or BBB?"))
    assert answer.refused is True
    assert answer.error is None
    assert adapter.calls == 0
    assert resolver.calls == []


@pytest.mark.criterion("C3")
@pytest.mark.metamorphic
@given(
    ticker=st.sampled_from(["AAA", "BBB", "ZZZ"]),
    fy=st.integers(2000, 2040),
    cik=st.one_of(st.none(), st.integers(1, 10**9)),
)
def test_the_answer_key_fields_choose_nothing(make_resolved, make_case, ticker, fy, cik):
    # oracle: requirement 1 — whatever the case's own ticker, year and cik, the same question
    # reads the same filing and sends the same messages
    base_arm, _, base_adapter, base_resolver, _ = make_resolved()
    arm, _, adapter, resolver, _ = make_resolved()
    question = "What was AAA total revenue in fiscal 2024?"
    base_arm.answer(make_case(question, ticker="AAA", fy=2024))
    arm.answer(make_case(question, ticker=ticker, fy=fy, cik=cik))
    assert resolver.calls == base_resolver.calls == [("AAA", 2024)]
    assert adapter.messages() == base_adapter.messages()
