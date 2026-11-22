"""C11: the record, the accounting, failures, the resolver's use and the cache across runs."""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.llm.run_budget import RunBudget, RunHalt

AAA = "0000000001"
BBB = "0000000002"


@pytest.mark.criterion("C11")
def test_the_record(make_arm, make_case, reply, cell, table_chunk, default_settings):
    # oracle: requirement 26, by hand, for one chunk scored 0.25 and one lookup
    table = table_chunk()
    tid = table["chunk_id"]
    env = make_arm(
        [table], reply_text=reply([cell("a", tid, "Net sales", "2024", "$1,200")], "a")
    )
    answer = env.arm.answer(make_case())
    record = dict(answer.selection)
    assert record.pop("rerank_ms") >= 0
    calculations = record.pop("calculations")
    assert record == {
        "status": "answered",
        "reason": None,
        "degraded": None,
        "scopes": [{"ciks": [AAA], "fiscal_years": [2024]}],
        "retrieved": [
            {"chunk_id": tid, "accession_no": "0000000001-25-000001", "ticker": "AAA",
             "fiscal_year": 2024, "section_key": "item_8", "kind": "table",
             "html_char_start": 0, "html_char_end": 100, "rerank_score": 0.25,
             "is_expansion": False},
        ],
        "rerank_calls": 1,
        "rerank_cache_hits": 0,
        "unverified": [],
        "retrieval_config": default_settings,
    }
    assert [c["expr"] for c in calculations] == ["a"]
    # tolerance: float product of exact integers
    assert calculations[0]["bindings"]["a"] == pytest.approx(1.2e9, rel=1e-12)
    assert calculations[0]["value"] == pytest.approx(1.2e9, rel=1e-12)
    payload = answer.to_dict()
    assert payload["selection"] == answer.selection
    assert json.loads(json.dumps(payload["selection"])) == answer.selection


@pytest.mark.criterion("C11")
def test_the_record_survives_a_provider_error(make_arm, make_case):
    # oracle: requirement 27 — what was retrieved stays visible when the call fails
    env = make_arm(provider_error=ValueError("provider broke"))
    answer = env.arm.answer(make_case())
    assert answer.error
    assert answer.selection["scopes"] == [{"ciks": [AAA], "fiscal_years": [2024]}]
    assert len(answer.selection["retrieved"]) == 1


@pytest.mark.criterion("C11")
def test_cost_is_the_router_plus_the_answer(make_arm, make_case, cost_of, router_model):
    # oracle: the closed form at MODEL_PRICING's rates, in the conftest
    env = make_arm()
    answer = env.arm.answer(make_case())
    expected = cost_of(router_model, 100, 50) + cost_of(env.arm.model, 1000, 500)
    # tolerance: float sum of a few rates
    assert answer.cost_usd == pytest.approx(expected, rel=1e-9)


@pytest.mark.criterion("C11")
@pytest.mark.metamorphic
def test_doubling_the_answer_tokens_doubles_their_cost(make_arm, make_case, cost_of, router_model):
    # oracle: the rates are linear
    env = make_arm(prompt_tokens=2000, completion_tokens=1000)
    answer = env.arm.answer(make_case())
    expected = cost_of(router_model, 100, 50) + 2 * cost_of(env.arm.model, 1000, 500)
    # tolerance: float sum of a few rates
    assert answer.cost_usd == pytest.approx(expected, rel=1e-9)


@pytest.mark.criterion("C11")
def test_each_answer_has_its_own_trace(make_arm, make_case):
    # oracle: requirement 27 — one trace per answer
    env = make_arm()
    first, second = env.arm.answer(make_case()), env.arm.answer(make_case())
    assert first.query_id and second.query_id
    assert first.query_id != second.query_id
    assert first.latency_ms >= 0


@pytest.mark.criterion("C11")
@pytest.mark.parametrize("where", ["router", "embedder", "qdrant", "reranker", "provider"])
def test_a_failure_is_an_error(
    make_arm, make_case, fake_embedder, fake_qdrant, fake_reranker, table_chunk, where
):
    # oracle: requirement 27 — reported, and not mistaken for an exclusion or a refusal
    error = ValueError("broke")
    kwargs = {
        "router": {"router_error": error},
        "embedder": {"embedder": fake_embedder(error=error)},
        "qdrant": {"client": fake_qdrant([table_chunk()], query_error=error)},
        "reranker": {"reranker": fake_reranker(error=error)},
        "provider": {"provider_error": error},
    }[where]
    answer = make_arm(**kwargs).arm.answer(make_case())
    assert answer.error
    assert not answer.error.startswith("excluded: ")
    assert answer.numeric_values == []


@pytest.mark.criterion("C11")
def test_run_halt_propagates_from_the_router_call(make_arm, make_case):
    # oracle: requirement 27 and the RAG rules — a budget of no calls stops the run
    env = make_arm(router_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        env.arm.answer(make_case())
    assert env.adapter.calls == 0


@pytest.mark.criterion("C11")
def test_run_halt_propagates_from_the_answer_call(make_arm, make_case):
    # oracle: requirement 27
    env = make_arm(run_budget=RunBudget(max_llm_calls=0))
    with pytest.raises(RunHalt):
        env.arm.answer(make_case())


@pytest.mark.criterion("C11")
def test_the_router_gets_the_question(make_arm, make_case):
    # oracle: requirement 4 — one router call, on the question
    env = make_arm()
    env.arm.answer(make_case("Tell me about AAA in fiscal 2024."))
    assert env.router_adapter.calls == 1
    assert "Tell me about AAA in fiscal 2024." in env.router_adapter.user_text()


@pytest.mark.criterion("C11")
@pytest.mark.parametrize(
    "companies, periods, filter_expected",
    [
        pytest.param([], ["fiscal 2024"], {"fiscal_year": [2024]}, id="no-company"),
        pytest.param(["Nobody Corp"], ["fiscal 2024"], {"fiscal_year": [2024]}, id="ambiguous"),
        pytest.param(["AAA"], ["sometime soon"], {"cik": [AAA]}, id="vague-period"),
    ],
)
def test_a_case_the_resolver_cannot_place_is_searched(
    make_arm, make_case, router_reply, conditions_of, reply, table_chunk,
    companies, periods, filter_expected,
):
    # oracle: requirement 6 — XbrlRag searches on what resolved; "Nobody Corp" matches nothing in
    # the entity table, and "sometime soon" names no year
    table = table_chunk()
    env = make_arm(
        [table],
        router_text=router_reply(companies, periods),
        reply_text=reply(passages=[{"chunk_id": table["chunk_id"], "quote": "Net sales"}]),
    )
    answer = env.arm.answer(make_case())
    assert answer.refused is False
    assert answer.selection["degraded"]
    assert env.adapter.calls == 1
    for prefetch in env.client.query_calls[0]["prefetch"]:
        assert conditions_of(prefetch.filter) == filter_expected


@pytest.mark.criterion("C11")
def test_a_comparison_searches_each_filer(make_arm, make_case, router_reply, payload):
    # oracle: requirements 5 and 9 — two filers, two searches, chunks from both reach the model
    env = make_arm(
        [payload("a", accession_no="acc-a"),
         payload("b", cik=BBB, ticker="BBB", company_name="Beta", accession_no="acc-b")],
        router_text=router_reply(["AAA", "BBB"], ["fiscal 2024"]),
    )
    env.arm.answer(make_case("Compare AAA and BBB in fiscal 2024."))
    assert len(env.client.query_calls) == 2
    assert [e["ticker"] for e in env.adapter.excerpts()] == ["AAA", "BBB"]


@pytest.mark.criterion("C11")
def test_a_repeat_run_pays_for_no_rerank(make_arm, make_case, make_config, fake_reranker):
    # oracle: requirements 13-14 and 26 — a second arm on the same cache: no rerank, same scores,
    # and the first run's rerank time is still reported
    config = make_config()
    reranker = fake_reranker({"OPERATIONS": 0.875})
    first = make_arm(config=config, reranker=reranker).arm.answer(make_case())
    second = make_arm(config=config, reranker=reranker).arm.answer(make_case())
    assert len(reranker.calls) == 1
    assert (first.selection["rerank_calls"], first.selection["rerank_cache_hits"]) == (1, 0)
    assert (second.selection["rerank_calls"], second.selection["rerank_cache_hits"]) == (0, 1)
    assert second.selection["retrieved"] == first.selection["retrieved"]
    assert second.selection["rerank_ms"] == first.selection["rerank_ms"]


@pytest.mark.criterion("C11")
@given(
    ticker=st.sampled_from([None, "AAA", "BBB", "ZZZ"]),
    fy=st.sampled_from([None, 2019, 2024, 2030]),
    cik=st.sampled_from([None, 1, 2]),
    kind=st.sampled_from(["lookup", "compute", "screen", "narrative"]),
    universe=st.lists(st.sampled_from(["AAA", "BBB", "CCC"]), max_size=3),
)
def test_the_case_answer_fields_change_nothing_sent(
    make_arm, make_case, conditions_of, ticker, fy, cik, kind, universe
):
    # oracle: requirement 4 — only the question reaches the search and the model
    baseline, varied = make_arm(), make_arm()
    baseline.arm.answer(make_case())
    varied.arm.answer(make_case(ticker=ticker, fy=fy, cik=cik, kind=kind, universe=universe))
    assert varied.adapter.user_text() == baseline.adapter.user_text()
    assert conditions_of(varied.client.query_calls[0]["prefetch"][0].filter) == {
        "cik": [AAA], "fiscal_year": [2024],
    }
