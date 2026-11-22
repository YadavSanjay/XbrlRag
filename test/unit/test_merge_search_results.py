import pytest
from qdrant_client.models import ScoredPoint

from src.retriever.base_retriever import BaseRetriever


def _make_point(point_id: str, score: float, year: int | None) -> ScoredPoint:
    return ScoredPoint(
        id=point_id,
        payload={"year": year} if year is not None else {},
        score=score,
        vector=None,
        # qdrant-client's ScoredPoint.version is a non-nullable int in current
        # releases; 0 is a valid placeholder for these merge-ordering tests.
        version=0,
    )


class TestMergeSearchResults:
    def setup_method(self):
        # Avoid heavy initializers on BaseRetriever
        self.retriever = BaseRetriever.__new__(BaseRetriever)

    def test_prefers_latest_year_then_highest_score(self):
        search_results = [
            ("source_a", _make_point("doc1", 0.2, 2022)),
            ("source_b", _make_point("doc1", 0.1, 2024)),  # newer year but lower score
            ("source_c", _make_point("doc1", 0.9, 2024)),  # same newest year, higher score
        ]

        merged = self.retriever.merge_search_results(search_results, top_k=5)

        assert len(merged) == 1
        _, point = merged[0]
        assert point.payload.get("year") == 2024
        assert pytest.approx(point.score) == 0.9

    def test_rrf_ranks_latest_year_before_fused_score(self):
        # doc_new has weaker fused score but newer year; doc_old has stronger fused score but older year
        search_results = [
            ("source_a", _make_point("doc_new", 0.1, 2024)),  # rank 1 in source_a
            ("source_a", _make_point("doc_old", 0.9, 2023)),  # rank 2 in source_a
            ("source_b", _make_point("doc_old", 0.8, 2023)),  # rank 1 in source_b, boosts fused score
        ]

        merged = self.retriever.merge_search_results(search_results, top_k=5)

        assert [point.id for _, point in merged[:2]] == ["doc_new", "doc_old"]

