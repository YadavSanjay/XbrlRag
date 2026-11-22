"""C4: the hybrid search request. Fusion is Qdrant's, so it is checked as a request."""

from __future__ import annotations

import pytest
from qdrant_client import models

AAA = "0000000001"
BBB = "0000000002"


def _by_vector(call):
    return {p.using: p for p in call["prefetch"]}


@pytest.mark.criterion("C4")
def test_the_request(make_retriever, payload, conditions_of):
    # oracle: requirement 8, by hand, with the values of requirement 1
    retriever, client, _, _ = make_retriever([payload("a")])
    retriever.retrieve("q", [{"ciks": [AAA], "fiscal_years": [2023, 2024]}])
    call = client.query_calls[0]
    assert call["collection_name"] == "html10k_text"
    assert call["limit"] == 200  # html-rag-fusion-order changed it from 30: 2 * prefetch_limit
    assert call["with_payload"] is True

    assert len(call["prefetch"]) == 2
    prefetches = _by_vector(call)
    dense, sparse = prefetches["text_dense"], prefetches["text_sparse"]
    assert dense.limit == 100 and sparse.limit == 100
    # tolerance: the fake's literals, passed through unchanged
    assert dense.query == pytest.approx([0.25, 0.5], abs=1e-12)
    assert sparse.query.indices == [1, 7]
    assert sparse.query.values == pytest.approx([0.5, 0.25], abs=1e-12)
    for prefetch in (dense, sparse):
        assert conditions_of(prefetch.filter) == {"cik": [AAA], "fiscal_year": [2023, 2024]}
        assert all(isinstance(c.match, models.MatchAny) for c in prefetch.filter.must)

    query = call["query"]
    assert isinstance(query, models.RrfQuery)
    assert query.rrf.k == 2
    weights = dict(zip([p.using for p in call["prefetch"]], query.rrf.weights, strict=True))
    # tolerance: exact config literals
    assert weights["text_dense"] == pytest.approx(2.0, abs=1e-12)
    assert weights["text_sparse"] == pytest.approx(1.0, abs=1e-12)


@pytest.mark.criterion("C4")
@pytest.mark.parametrize(
    "scope, expected",
    [
        pytest.param({"ciks": [], "fiscal_years": []}, None, id="none"),
        pytest.param({"ciks": [AAA], "fiscal_years": []}, {"cik": [AAA]}, id="filer"),
        pytest.param({"ciks": [], "fiscal_years": [2024]}, {"fiscal_year": [2024]}, id="year"),
        pytest.param({"ciks": [AAA, BBB], "fiscal_years": [2024]},
                     {"cik": [AAA, BBB], "fiscal_year": [2024]}, id="shared-search"),
    ],
)
def test_the_filter(make_retriever, payload, conditions_of, scope, expected):
    # oracle: requirement 8 — a condition only for what is there, and None with neither
    retriever, client, _, _ = make_retriever([payload("a")])
    retriever.retrieve("q", [scope])
    for prefetch in client.query_calls[0]["prefetch"]:
        if expected is None:
            assert prefetch.filter is None
        else:
            assert conditions_of(prefetch.filter) == expected


@pytest.mark.criterion("C4")
def test_one_embedding_and_one_search_per_scope(make_retriever, payload):
    # oracle: requirements 7-8 — two scopes are two searches on one embedding
    retriever, client, embedder, _ = make_retriever([payload("a")])
    retriever.retrieve(
        "the question",
        [{"ciks": [AAA], "fiscal_years": []}, {"ciks": [BBB], "fiscal_years": []}],
    )
    assert embedder.batches == [[("query", "the question")]]
    assert len(client.query_calls) == 2


@pytest.mark.criterion("C4")
@pytest.mark.metamorphic
def test_other_settings_reach_the_request(make_retriever, payload):
    # oracle: requirement 1 — the numbers are configuration, none of them is in code
    retriever, client, _, _ = make_retriever(
        [payload("a")], rrf_k=7, dense_weight=3.0, sparse_weight=0.5, prefetch_limit=40,
        rerank_candidates=12, context_top_k=4,
    )
    retriever.retrieve("q", [{"ciks": [], "fiscal_years": []}])
    call = client.query_calls[0]
    assert call["limit"] == 80  # html-rag-fusion-order changed it from 12: 2 * prefetch_limit
    assert all(p.limit == 40 for p in call["prefetch"])
    assert call["query"].rrf.k == 7
    weights = dict(zip([p.using for p in call["prefetch"]], call["query"].rrf.weights, strict=True))
    # tolerance: exact config literals
    assert weights["text_dense"] == pytest.approx(3.0, abs=1e-12)
    assert weights["text_sparse"] == pytest.approx(0.5, abs=1e-12)
