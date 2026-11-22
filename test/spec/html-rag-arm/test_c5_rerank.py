"""C5: what the reranker sees, and the order that comes back."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

AAA = "0000000001"
BBB = "0000000002"
CCC = "0000000003"
ALL = {"ciks": [AAA], "fiscal_years": [2024]}


def _three(payload):
    return [
        payload("a", start=0, end=10),
        payload("b", start=10, end=20),
        payload("c", start=20, end=30),
    ]


@pytest.mark.criterion("C5")
def test_the_reranker_sees_filer_year_and_section(make_retriever, payload):
    # oracle: requirement 9 — html10k-chunks requirement 16's string, by hand, in candidate order
    retriever, _, _, reranker = make_retriever(_three(payload))
    retriever.retrieve("q", [ALL])
    assert reranker.calls == [
        ("q", ["AAA FY2024 Item 7. MD&A :: passage a",
               "AAA FY2024 Item 7. MD&A :: passage b",
               "AAA FY2024 Item 7. MD&A :: passage c"]),
    ]


@pytest.mark.criterion("C5")
def test_ranked_highest_first_and_cut(make_retriever, payload):
    # oracle: requirement 10 — scores 0.125, 0.875, 0.5 sort b, c, a by hand; the cut keeps two
    retriever, _, _, _ = make_retriever(
        _three(payload),
        scores={"passage a": 0.125, "passage b": 0.875, "passage c": 0.5},
        context_top_k=2,
    )
    result = retriever.retrieve("q", [ALL])
    assert [c.text for c in result.chunks] == ["passage b", "passage c"]
    # tolerance: exact binary fractions, passed through
    assert result.chunks[0].rerank_score == pytest.approx(0.875, abs=1e-12)
    assert result.rerank_calls == 1
    assert result.rerank_cache_hits == 0
    assert result.rerank_ms >= 0


@pytest.mark.criterion("C5")
def test_a_chunk_carries_its_payload(make_retriever, table_chunk):
    # oracle: the Interface's field list, against the hand-built payload
    source = table_chunk(start=40, end=90, part=1, parts=2)
    retriever, _, _, _ = make_retriever([source])
    chunk = retriever.retrieve("q", [ALL]).chunks[0]
    assert chunk.chunk_id == source["chunk_id"]
    assert chunk.accession_no == "0000000001-25-000001"
    assert chunk.text == source["text"]
    assert chunk.cik == AAA
    assert (chunk.ticker, chunk.company_name) == ("AAA", "Alpha Holdings")
    assert (chunk.fiscal_year, chunk.form_type) == (2024, "10-K")
    assert (chunk.section_key, chunk.section_label) == ("item_8", "Item 8. Financial Statements")
    assert (chunk.contains_table, chunk.table_index, chunk.part, chunk.parts) == (True, 3, 1, 2)
    assert (chunk.html_char_start, chunk.html_char_end) == (40, 90)
    assert (chunk.prev_chunk_id, chunk.next_chunk_id) == (None, None)
    assert chunk.is_expansion is False


@pytest.mark.criterion("C5")
def test_two_filers_are_reranked_apart_and_both_kept(make_retriever, payload):
    # oracle: requirements 9-10 — BBB scores below every AAA chunk, and still keeps its two
    def filer(cik: str, ticker: str, name: str) -> list[dict]:
        return [
            payload(f"{name}{i}", cik=cik, ticker=ticker, accession_no=f"acc-{ticker}",
                    start=i * 10, end=i * 10 + 10)
            for i in range(3)
        ]

    retriever, _, _, reranker = make_retriever(
        filer(AAA, "AAA", "x") + filer(BBB, "BBB", "y"),
        scores={"passage x": 0.75, "passage y": 0.0625},
        context_top_k=2,
    )
    result = retriever.retrieve(
        "q", [{"ciks": [AAA], "fiscal_years": []}, {"ciks": [BBB], "fiscal_years": []}]
    )
    assert [c.ticker for c in result.chunks] == ["AAA", "AAA", "BBB", "BBB"]
    assert len(reranker.calls) == 2
    assert result.rerank_calls == 2


@pytest.mark.criterion("C5")
def test_only_the_candidates_are_reranked(make_retriever, payload):
    # oracle: requirement 8's limit — three stored, two candidates
    retriever, _, _, reranker = make_retriever(
        _three(payload), rerank_candidates=2, context_top_k=2
    )
    retriever.retrieve("q", [ALL])
    assert len(reranker.calls[0][1]) == 2


@pytest.mark.criterion("C5")
def test_an_empty_scope_makes_no_rerank_call(make_retriever, payload):
    # oracle: requirement 9 — CCC has no chunks
    retriever, _, _, reranker = make_retriever(_three(payload))
    result = retriever.retrieve(
        "q", [{"ciks": [AAA], "fiscal_years": []}, {"ciks": [CCC], "fiscal_years": []}]
    )
    assert len(reranker.calls) == 1
    assert len(result.chunks) == 3

    nothing, _, _, none_called = make_retriever([])
    assert nothing.retrieve("q", [ALL]).chunks == []
    assert none_called.calls == []


@pytest.mark.criterion("C5")
@pytest.mark.parametrize("where", ["embedder", "qdrant", "reranker"])
def test_a_failure_is_loud(
    make_retriever, fake_embedder, fake_qdrant, fake_reranker, payload, where
):
    # oracle: requirement 11
    error = RuntimeError("service down")
    kwargs = {
        "embedder": {"embedder": fake_embedder(error=error)},
        "qdrant": {"client": fake_qdrant([payload("a")], query_error=error)},
        "reranker": {"reranker": fake_reranker(error=error)},
    }[where]
    retriever, _, _, _ = make_retriever([payload("a")], **kwargs)
    with pytest.raises(RuntimeError, match="service down"):
        retriever.retrieve("q", [ALL])


@pytest.mark.criterion("C5")
@given(
    scores=st.lists(st.integers(0, 16).map(lambda n: n / 16), min_size=1, max_size=8),
    top_k=st.integers(1, 8),
)
def test_the_order_is_the_sort(make_retriever, fake_reranker, payload, scores, top_k):
    # oracle: requirement 10 — computed here: score descending, ties in candidate order
    payloads = [payload(f"m{i}", start=i * 10, end=i * 10 + 10) for i in range(len(scores))]
    retriever, _, _, _ = make_retriever(
        payloads, reranker=fake_reranker(raw=scores), expand_neighbors=False,
        context_top_k=top_k,
    )
    expected = sorted(range(len(scores)), key=lambda i: (-scores[i], i))[:top_k]
    result = retriever.retrieve("q", [ALL])
    assert [c.text for c in result.chunks] == [f"passage m{i}" for i in expected]
