"""C1: the candidates are every fused point, sorted by score then chunk_id, cut to the setting."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from qdrant_client import models

from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.retriever.html10k.config import load_html10k_config
from src.retriever.html10k.rerank_cache import RerankResult
from src.retriever.html10k.retriever import Html10kRetriever
from src.vectorstores.html10k_qdrant_vectordb_client import embedding_input

SCOPE = {"ciks": ["0000000001"], "fiscal_years": [2024]}


def _payload(chunk_id: str) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id, "accession_no": "0000000001-24-000001",
        "text": f"text of {chunk_id}", "cik": "0000000001", "ticker": "TST", "company_name": "Test",
        "fiscal_year": 2024, "form_type": "10-K", "section_key": "notes", "section_label": "Notes",
        "contains_table": False, "table_index": None, "part": 0, "parts": 1,
        "html_char_start": 0, "html_char_end": 1, "prev_chunk_id": None, "next_chunk_id": None,
    }


class _Qdrant:
    """Qdrant, a network boundary: returns the given scored points, in the given order."""

    def __init__(self, response: list[tuple[str, float]]) -> None:
        self.response = response
        self.limits: list[int] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.limits.append(kwargs["limit"])
        points = [
            models.ScoredPoint(id=i, version=0, score=score, payload=_payload(chunk_id))
            for i, (chunk_id, score) in enumerate(self.response[: kwargs["limit"]])
        ]
        return SimpleNamespace(points=points)

    def retrieve(self, **kwargs: Any) -> list[Any]:
        return []


class _Embedder:
    """The embedder, a network boundary."""

    def embed_texts(self, texts: list[tuple[str, str]]) -> dict[str, Any]:
        return {texts[0][0]: (DenseEmbedding(values=[1.0]), SparseEmbedding(indices=[1], values=[1.0]))}


class _Reranker:
    """The reranker, a network boundary: records what it is sent; every score equal."""

    def __init__(self) -> None:
        self.documents: list[str] = []

    def rerank(self, query: str, documents: list[str]) -> RerankResult:
        self.documents = list(documents)
        return RerankResult(scores=[0.1] * len(documents), cache_hit=False, elapsed_ms=1.0)


def _sent(response: list[tuple[str, float]]) -> tuple[list[str], list[int]]:
    qdrant, reranker = _Qdrant(response), _Reranker()
    retriever = Html10kRetriever(
        embedder=_Embedder(), client=qdrant, reranker=reranker, config=load_html10k_config()
    )
    retriever.retrieve("question", [SCOPE])
    return reranker.documents, qdrant.limits


def _documents(chunk_ids: list[str]) -> list[str]:
    return [embedding_input(_payload(c)) for c in chunk_ids]


@pytest.mark.criterion("C1")
def test_a_tie_across_the_cut_is_settled_by_chunk_id():
    # oracle: requirements 1-2 by hand, committed prefetch_limit 100 and rerank_candidates 50.
    # c50-c59 score 0.9 and lead, in chunk_id order; c00-c49 tie at 0.5, so c00-c39 fill the
    # remaining 40 places. Qdrant returns them lowest first, and backwards within each score.
    top = [(f"c{n:02d}", 0.9) for n in range(59, 49, -1)]
    rest = [(f"c{n:02d}", 0.5) for n in range(49, -1, -1)]
    documents, limits = _sent(rest + top)
    assert limits == [200]
    expected = [f"c{n:02d}" for n in range(50, 60)] + [f"c{n:02d}" for n in range(40)]
    assert documents == _documents(expected)


@pytest.mark.criterion("C1")
def test_chunk_ids_compare_as_strings():
    # oracle: requirement 2 by hand: as strings, "c10" < "c9"; the higher score still leads
    documents, _ = _sent([("c9", 0.5), ("c10", 0.5), ("c2", 0.7)])
    assert documents == _documents(["c2", "c10", "c9"])


_responses = st.lists(
    st.tuples(st.integers(min_value=0, max_value=400), st.sampled_from([0.125, 0.25, 0.5, 1.0])),
    min_size=1, max_size=120, unique_by=lambda t: t[0],
).map(lambda rows: [(f"chunk-{n}", score) for n, score in rows])


@pytest.mark.criterion("C1")
@settings(max_examples=60, deadline=None)
@given(response=_responses, data=st.data())
def test_any_order_from_qdrant_gives_the_same_candidates(response, data):
    # oracle: requirements 2-3: a plain sort in the test, and the result of a shuffled response
    shuffled = data.draw(st.permutations(response))
    first, _ = _sent(response)
    second, _ = _sent(list(shuffled))
    expected = [c for c, _ in sorted(response, key=lambda t: (-t[1], t[0]))[:50]]
    assert first == _documents(expected)
    assert second == first
