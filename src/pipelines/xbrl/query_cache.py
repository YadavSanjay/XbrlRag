"""WS-4 / B1 — two-tier semantic cache for whole-query XBRL answers.

SEC-10K Q&A has high query repetition, and the answer for a given corpus is stable,
so caching the pipeline's answer for a query avoids re-running the whole
decompose→route→plan→execute→synthesize→verify chain (and its LLM spend). Named the
single biggest cost lever in the design series.

Two tiers:
  * **L1 — exact match**: normalized-query string → cached ``XbrlRagResponse`` dict.
  * **L2 — vector similarity** (opt-in): embeds the query and returns a cached answer
    whose query is within a cosine-similarity threshold, catching paraphrases. L2 is
    off by default because it needs the embedder (BGE-M3), which a purely numeric
    query would not otherwise load.

**Correctness / invalidation.** The cache is flushed when the shared
``query_cache_epoch`` (Postgres) changes — the publish gate bumps that epoch when a
new filing is published (a different process). :class:`CacheEpochReader` reads it with
a short TTL so a stale answer is never served past a publish (bounded by the TTL).

Everything is **disabled by default** (``XBRLRAG_QUERY_CACHE`` unset) and in-process
(no new dependency); enabling it changes nothing about correctness because of the
epoch flush.
"""

from __future__ import annotations

import copy
import math
import os
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

from src.logging_config import get_logger

logger = get_logger(__name__)


def _flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _xbrl_db_params() -> dict:
    from src.db.conn import conn_params

    return conn_params()


# ----------------------------------------------------------------------
# Cross-process epoch (invalidation signal)
# ----------------------------------------------------------------------


def read_cache_epoch(conn_params: dict | None = None) -> int:
    """Read the shared cache epoch from Postgres. Returns 0 if the table is
    absent or the DB is unreachable (i.e. never spuriously invalidates)."""
    try:
        with psycopg2.connect(**(conn_params or _xbrl_db_params())) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT epoch FROM query_cache_epoch WHERE singleton = TRUE"
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def bump_cache_epoch(conn) -> None:
    """Increment the shared cache epoch on an existing connection (called by the
    publish gate after it commits newly-published filings). Best-effort; never
    raises into the ingest path."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO query_cache_epoch (singleton, epoch, updated_at) "
                "VALUES (TRUE, 1, now()) "
                "ON CONFLICT (singleton) DO UPDATE SET "
                "epoch = query_cache_epoch.epoch + 1, updated_at = now()"
            )
    except Exception as exc:  # pragma: no cover - best-effort
        logger.debug("bump_cache_epoch failed (non-fatal): %s", exc)


class CacheEpochReader:
    """Reads the shared cache epoch with a short TTL so the query path does not hit
    Postgres on every request. Staleness after a publish is bounded by the TTL."""

    def __init__(self, conn_params: dict | None = None, ttl_seconds: float = 15.0):
        self._conn_params = conn_params or _xbrl_db_params()
        self._ttl = ttl_seconds
        self._epoch = 0
        self._last_read = 0.0
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> CacheEpochReader:
        return cls(
            ttl_seconds=float(os.environ.get("XBRLRAG_QUERY_CACHE_EPOCH_TTL", "15"))
        )

    def current(self) -> int:
        now = time.time()
        with self._lock:
            if now - self._last_read < self._ttl:
                return self._epoch
        epoch = read_cache_epoch(self._conn_params)
        with self._lock:
            self._epoch = epoch
            self._last_read = now
        return epoch


# ----------------------------------------------------------------------
# The cache
# ----------------------------------------------------------------------


def _unit(vec: list[float]) -> list[float] | None:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return None
    return [x / norm for x in vec]


def _cosine_unit(a: list[float], b: list[float]) -> float:
    # both are unit vectors -> cosine is the dot product
    return sum(x * y for x, y in zip(a, b))


class SemanticQueryCache:
    """In-process L1 (exact) + optional L2 (vector) answer cache, LRU-bounded."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        max_entries: int = 256,
        l2_enabled: bool = False,
        sim_threshold: float = 0.97,
    ):
        self.enabled = enabled
        self.max_entries = max(1, max_entries)
        self.l2_enabled = l2_enabled
        self.sim_threshold = sim_threshold
        self._l1: OrderedDict[str, dict] = OrderedDict()
        self._l2: list[tuple[list[float], str]] = []  # (unit_vec, l1_key)
        self._epoch: int | None = None
        self._lock = threading.Lock()
        self.hits_l1 = 0
        self.hits_l2 = 0
        self.misses = 0

    @classmethod
    def from_env(cls) -> SemanticQueryCache:
        return cls(
            enabled=_flag("XBRLRAG_QUERY_CACHE"),
            max_entries=int(os.environ.get("XBRLRAG_QUERY_CACHE_MAX", "256")),
            l2_enabled=_flag("XBRLRAG_QUERY_CACHE_L2"),
            sim_threshold=float(os.environ.get("XBRLRAG_QUERY_CACHE_SIM", "0.97")),
        )

    @staticmethod
    def _normalize(query: str) -> str:
        return " ".join((query or "").lower().split())

    def maybe_invalidate(self, epoch: int | None) -> None:
        """Flush the cache if the shared epoch changed since last seen."""
        with self._lock:
            if self._epoch is not None and epoch is not None and epoch != self._epoch:
                self._l1.clear()
                self._l2.clear()
                logger.info(
                    "query cache flushed on epoch change %s -> %s", self._epoch, epoch
                )
            self._epoch = epoch

    def get(
        self, query: str, embed_fn: Callable[[str], list[float]] | None = None
    ) -> dict | None:
        if not self.enabled:
            return None
        key = self._normalize(query)
        with self._lock:
            if key in self._l1:
                self._l1.move_to_end(key)
                self.hits_l1 += 1
                return copy.deepcopy(self._l1[key])

        if self.l2_enabled and embed_fn is not None:
            vec = _unit(embed_fn(query))
            if vec is not None:
                with self._lock:
                    best_key, best_sim = None, -1.0
                    for v, k in self._l2:
                        s = _cosine_unit(vec, v)
                        if s > best_sim:
                            best_sim, best_key = s, k
                    if (
                        best_key is not None
                        and best_sim >= self.sim_threshold
                        and best_key in self._l1
                    ):
                        self._l1.move_to_end(best_key)
                        self.hits_l2 += 1
                        return copy.deepcopy(self._l1[best_key])

        with self._lock:
            self.misses += 1
        return None

    def put(
        self,
        query: str,
        response: dict,
        embed_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        if not self.enabled:
            return
        key = self._normalize(query)
        vec = None
        if self.l2_enabled and embed_fn is not None:
            vec = _unit(embed_fn(query))
        with self._lock:
            self._l1[key] = copy.deepcopy(response)
            self._l1.move_to_end(key)
            self._l2 = [(v, k) for (v, k) in self._l2 if k != key]
            if vec is not None:
                self._l2.append((vec, key))
            while len(self._l1) > self.max_entries:
                old_key, _ = self._l1.popitem(last=False)
                self._l2 = [(v, k) for (v, k) in self._l2 if k != old_key]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits_l1 + self.hits_l2 + self.misses
            return {
                "enabled": self.enabled,
                "l2_enabled": self.l2_enabled,
                "entries": len(self._l1),
                "hits_l1": self.hits_l1,
                "hits_l2": self.hits_l2,
                "misses": self.misses,
                "hit_rate": (self.hits_l1 + self.hits_l2) / total if total else 0.0,
            }
