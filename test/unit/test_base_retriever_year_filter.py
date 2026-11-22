"""The PDF legacy retriever's year filter (docs/specs/no-default-year.md R6).

The shared year reader has no default year; the legacy retriever keeps searching its corpus
year for a query that writes none, from its own named constant, and the query's years otherwise.
"""

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.retriever import base_retriever
from src.retriever.base_retriever import LEGACY_DEFAULT_FISCAL_YEARS, BaseRetriever


class FakeEmbedder:
    """Returns a fixed dense/sparse pair for the one query text."""

    def embed_texts(self, texts: list[tuple[str, str]]) -> dict[str, tuple[str, str]]:
        return {key: ("dense", "sparse") for key, _text in texts}


class FakeVectorClient:
    """Records the year filter of each hybrid text search (the Qdrant I/O boundary)."""

    def __init__(self) -> None:
        self.filters: list[list[str]] = []

    def hybrid_query_text(self, **kwargs: Any) -> SimpleNamespace:
        self.filters.append(kwargs["fiscal_years_in_query"])
        return SimpleNamespace(points=[])


@pytest.fixture
def retriever() -> BaseRetriever:
    # Skip __init__: it builds the real embedder, Qdrant client and reranker.
    instance = BaseRetriever.__new__(BaseRetriever)
    instance.embedding_generator = FakeEmbedder()
    instance.vector_db_client = FakeVectorClient()
    return instance


def test_the_legacy_default_is_its_corpus_year() -> None:
    assert LEGACY_DEFAULT_FISCAL_YEARS == [2024]


class _Sink(logging.Handler):
    """Collects records; not caplog, as configs/logging.yaml stops project loggers propagating."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_a_yearless_query_searches_the_legacy_default_year(retriever) -> None:
    log = base_retriever.logger
    sink, previous = _Sink(), log.level
    log.addHandler(sink)
    log.setLevel(logging.INFO)
    try:
        retriever.retrieve_chunks("What are Apple's main risk factors?")
    finally:
        log.removeHandler(sink)
        log.setLevel(previous)
    assert retriever.vector_db_client.filters == [["2024"]]
    assert any("legacy default" in r.getMessage() for r in sink.records)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Apple revenue in fiscal 2022", ["2022"]),
        ("Compare FY2021 and FY2023 margins", ["2021", "2023"]),
    ],
)
def test_a_query_with_years_searches_those_years(retriever, query, expected) -> None:
    retriever.retrieve_chunks(query)
    assert retriever.vector_db_client.filters == [expected]
