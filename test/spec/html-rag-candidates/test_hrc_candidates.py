"""C1: the reranker gets 50 candidates, so a table past the old cut can reach the answer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.retriever.html10k.config import load_html10k_config
from src.retriever.html10k.rerank_cache import RerankResult
from src.retriever.html10k.retriever import Html10kRetriever

SCOPE = {"ciks": ["0000000001"], "fiscal_years": [2024]}


def _payload(position: int) -> dict[str, Any]:
    return {
        "chunk_id": f"chunk-{position}", "accession_no": "0000000001-24-000001",
        "text": f"table {position}", "cik": "0000000001", "ticker": "TST", "company_name": "Test",
        "fiscal_year": 2024, "form_type": "10-K", "section_key": "notes", "section_label": "Notes",
        "contains_table": True, "table_index": position, "part": 0, "parts": 1,
        "html_char_start": position, "html_char_end": position + 1, "prev_chunk_id": None,
        "next_chunk_id": None,
    }


class _Qdrant:
    """Qdrant, a network boundary: answers a fused search with as many points as it is asked."""

    def __init__(self) -> None:
        self.limits: list[int] = []

    def query_points(self, **kwargs: Any) -> Any:
        self.limits.append(kwargs["limit"])
        # html-rag-fusion-order sorts by score, so each point carries one that falls with position.
        points = [
            SimpleNamespace(payload=_payload(n), score=1.0 / n) for n in range(1, kwargs["limit"] + 1)
        ]
        return SimpleNamespace(points=points)

    def retrieve(self, **kwargs: Any) -> list[Any]:
        return []


class _Embedder:
    """The embedder, a network boundary."""

    def embed_texts(self, texts: list[tuple[str, str]]) -> dict[str, Any]:
        return {texts[0][0]: (DenseEmbedding(values=[1.0]), SparseEmbedding(indices=[1], values=[1.0]))}


class _Reranker:
    """The reranker, a network boundary: only the chunk at *best* scores well."""

    def __init__(self, best: int) -> None:
        self.best = best
        self.documents = 0

    def rerank(self, query: str, documents: list[str]) -> RerankResult:
        self.documents = len(documents)
        scores = [0.9 if f"table {self.best}" == d.split(" :: ")[1] else 0.1 for d in documents]
        return RerankResult(scores=scores, cache_hit=False, elapsed_ms=1.0)


def _retrieve(best: int) -> tuple[Any, _Qdrant, _Reranker]:
    qdrant, reranker = _Qdrant(), _Reranker(best)
    retriever = Html10kRetriever(
        embedder=_Embedder(), client=qdrant, reranker=reranker, config=load_html10k_config()
    )
    return retriever.retrieve("question", [SCOPE]), qdrant, reranker


@pytest.mark.criterion("C1")
def test_the_committed_settings(monkeypatch):
    # oracle: html-rag-arm requirement 1's table by hand, with requirement 1 of this spec
    monkeypatch.delenv("XBRLRAG_HTML10K_RERANK_CACHE", raising=False)
    config = load_html10k_config()
    assert config.rerank_candidates == 50
    # tolerance: exact literals, approx only because the rubric asks for it on floats
    assert config.dense_weight == pytest.approx(2.0, abs=1e-12)
    assert config.sparse_weight == pytest.approx(1.0, abs=1e-12)
    assert config.expansion_score_threshold == pytest.approx(0.5, abs=1e-12)
    assert (config.rrf_k, config.prefetch_limit, config.context_top_k) == (2, 100, 20)
    assert (config.expansion_max_chunks, config.max_filer_searches) == (20, 10)
    assert (config.table_max_chars, config.embed_batch_size) == (1200, 32)
    assert config.reranker_model == "jinaai/jina-reranker-v3"
    assert config.expand_neighbors is True


@pytest.mark.criterion("C1")
def test_the_last_candidate_reaches_the_reranker():
    # oracle: requirement 3 by hand: one search, all 50 reranked, 20 kept
    out, qdrant, reranker = _retrieve(50)
    assert qdrant.limits == [200]  # html-rag-fusion-order changed it from 50: 2 * prefetch_limit
    assert reranker.documents == 50
    assert len(out.chunks) == 20
    assert out.chunks[0].chunk_id == "chunk-50"


@pytest.mark.criterion("C1")
@settings(max_examples=20, deadline=None)
@given(position=st.integers(min_value=31, max_value=50))
def test_a_table_past_the_old_cut_is_returned_first(position):
    # oracle: requirement 3: the chunk that scores highest leads, wherever fusion placed it
    out, _, _ = _retrieve(position)
    assert out.chunks[0].chunk_id == f"chunk-{position}"
