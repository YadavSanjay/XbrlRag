# Rerank scores, kept on disk, so that a repeat run of html_rag does not pay for them again.
#
# The key is the model, the query and the exact ordered documents, so a changed chunk, a new load
# or another reranker is a miss and no index version needs tracking. The order is in the key
# because jina-reranker-v3 is listwise. Scores are stored as JSON, whose floats read back exactly,
# since a cache that rounded could change a run's order.
#
# Spec: docs/specs/html-rag-arm.md.

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, NamedTuple

_BUSY_TIMEOUT_S = 30.0  # another process may be writing to a shared cache file
_SCHEMA = """
    CREATE TABLE IF NOT EXISTS rerank_scores (
        key        TEXT PRIMARY KEY,
        scores     TEXT NOT NULL,
        elapsed_ms REAL NOT NULL
    )
"""


class RerankResult(NamedTuple):
    """One rerank's outcome.

    Attributes:
        scores: The scores, in input order.
        cache_hit: Whether they came from the cache.
        elapsed_ms: How long the original rerank took, unchanged on a hit.
    """

    scores: list[float]
    cache_hit: bool
    elapsed_ms: float


class CachedReranker:
    """A reranker with its scores cached by model, query and the exact documents.

    Thread-safe. A hit never waits for a rerank in progress. Misses run one at a time, because the
    reranker is CPU-bound, and a miss re-reads the cache once it holds the lock, so two equal
    concurrent calls make one call to the reranker.
    """

    def __init__(self, inner: Any, *, model: str, cache_path: Path) -> None:
        """Keep the reranker and open the cache.

        Args:
            inner: The reranker, with ``rerank(query, documents) -> list[float]``.
            model: The reranker's model name, which is part of the cache key.
            cache_path: The cache file; created when missing.
        """
        self._inner = inner
        self._model = model
        self._path = cache_path
        self._rerank_lock = threading.Lock()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=_BUSY_TIMEOUT_S)

    def _key(self, query: str, documents: list[str]) -> str:
        request = json.dumps([self._model, query, documents], ensure_ascii=False)
        return hashlib.sha256(request.encode("utf-8")).hexdigest()

    def _read(self, key: str) -> RerankResult | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT scores, elapsed_ms FROM rerank_scores WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else RerankResult(json.loads(row[0]), True, row[1])

    def _write(self, key: str, scores: list[float], elapsed_ms: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO rerank_scores (key, scores, elapsed_ms) VALUES (?, ?, ?)",
                (key, json.dumps(scores), elapsed_ms),
            )

    def rerank(self, query: str, documents: Sequence[str]) -> RerankResult:
        """Score documents, from the cache when it holds this exact request.

        Args:
            query: The question.
            documents: The candidates, in candidate order.

        Returns:
            The scores in input order, whether they came from the cache, and how long the
            original rerank took.

        Raises:
            ValueError: The reranker returned the wrong number of scores, or a non-finite one.
        """
        texts = list(documents)
        if not texts:
            return RerankResult([], False, 0.0)
        key = self._key(query, texts)
        found = self._read(key)
        if found is not None:
            return found
        with self._rerank_lock:
            found = self._read(key)
            if found is not None:
                return found
            started = time.perf_counter()
            scores = [float(score) for score in self._inner.rerank(query, texts)]
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if len(scores) != len(texts):
                raise ValueError(f"{len(scores)} scores for {len(texts)} documents")
            if not all(math.isfinite(score) for score in scores):
                raise ValueError(f"a score is not finite: {scores}")
            self._write(key, scores, elapsed_ms)
        return RerankResult(scores, False, elapsed_ms)
