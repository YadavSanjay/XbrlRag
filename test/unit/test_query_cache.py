"""Unit tests for the WS-4 / B1 semantic query cache (no DB, no LLM)."""
import pytest

import src.pipelines.xbrl.query_cache as qc
from src.pipelines.xbrl.query_cache import (
    CacheEpochReader,
    SemanticQueryCache,
    bump_cache_epoch,
    read_cache_epoch,
)

# ---------------------------------------------------------------------------
# L1 / L2 / eviction / invalidation
# ---------------------------------------------------------------------------

class TestSemanticQueryCache:
    def test_disabled_is_noop(self):
        c = SemanticQueryCache(enabled=False)
        c.put("q", {"a": 1})
        assert c.get("q") is None

    def test_l1_exact_hit_is_normalized(self):
        c = SemanticQueryCache(enabled=True)
        c.put("What was NVDA revenue in FY2026?", {"answer_summary": "X"})
        assert c.get("what was   nvda   revenue in fy2026?") == {"answer_summary": "X"}
        assert c.hits_l1 == 1

    def test_miss_counts(self):
        c = SemanticQueryCache(enabled=True)
        c.put("a", {"v": 1})
        assert c.get("b") is None
        assert c.misses == 1

    def test_returns_a_copy_not_the_stored_object(self):
        c = SemanticQueryCache(enabled=True)
        stored = {"answer_summary": "X", "details": [1]}
        c.put("q", stored)
        got = c.get("q")
        got["details"].append(2)
        assert c.get("q")["details"] == [1]        # mutation didn't leak into the cache

    def test_lru_eviction(self):
        c = SemanticQueryCache(enabled=True, max_entries=2)
        c.put("a", {"v": 1})
        c.put("b", {"v": 2})
        assert c.get("a") is not None              # touch 'a' so 'b' is LRU
        c.put("c", {"v": 3})                        # evicts 'b'
        assert c.get("b") is None
        assert c.get("a") is not None and c.get("c") is not None

    def test_epoch_change_flushes(self):
        c = SemanticQueryCache(enabled=True)
        c.maybe_invalidate(5)
        c.put("q", {"v": 1})
        assert c.get("q") is not None
        c.maybe_invalidate(6)                       # publish bumped the epoch
        assert c.get("q") is None

    def test_same_epoch_does_not_flush(self):
        c = SemanticQueryCache(enabled=True)
        c.maybe_invalidate(5)
        c.put("q", {"v": 1})
        c.maybe_invalidate(5)
        assert c.get("q") is not None

    def test_l2_vector_hit_above_threshold(self):
        c = SemanticQueryCache(enabled=True, l2_enabled=True, sim_threshold=0.98)
        vectors = {
            "revenue of nvidia fy2026": [1.0, 0.0, 0.0],
            "nvidia revenue fiscal 2026": [0.99, 0.14, 0.0],   # cosine ~0.99 vs above
            "apple cash flow": [0.0, 0.0, 1.0],
        }
        embed = lambda q: vectors[q]  # noqa: E731
        c.put("revenue of nvidia fy2026", {"a": "nvda-rev"}, embed_fn=embed)
        # A paraphrase with a near-identical vector hits via L2.
        assert c.get("nvidia revenue fiscal 2026", embed_fn=embed) == {"a": "nvda-rev"}
        assert c.hits_l2 == 1
        # A dissimilar query misses.
        assert c.get("apple cash flow", embed_fn=embed) is None

    def test_l2_below_threshold_misses(self):
        c = SemanticQueryCache(enabled=True, l2_enabled=True, sim_threshold=0.999)
        vectors = {"q1": [1.0, 0.0], "q2": [0.9, 0.436]}   # cosine ~0.9
        embed = lambda q: vectors[q]  # noqa: E731
        c.put("q1", {"a": 1}, embed_fn=embed)
        assert c.get("q2", embed_fn=embed) is None

    def test_stats_hit_rate(self):
        c = SemanticQueryCache(enabled=True)
        c.put("a", {"v": 1})
        c.get("a"); c.get("a"); c.get("b")
        s = c.stats()
        assert s["hits_l1"] == 2 and s["misses"] == 1
        assert s["hit_rate"] == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# Epoch helpers
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self._sink.append(sql)


class _FakeConn:
    def __init__(self):
        self.executed = []

    def cursor(self):
        return _FakeCursor(self.executed)


class TestEpochHelpers:
    def test_read_returns_zero_when_db_unreachable(self):
        # A bogus host makes psycopg2.connect raise; read must swallow -> 0.
        assert read_cache_epoch({"host": "256.256.256.256", "port": 1,
                                 "dbname": "x", "user": "x", "password": "x"}) == 0

    def test_bump_runs_upsert(self):
        conn = _FakeConn()
        bump_cache_epoch(conn)
        assert any("query_cache_epoch" in s and "ON CONFLICT" in s for s in conn.executed)

    def test_epoch_reader_caches_within_ttl(self, monkeypatch):
        calls = {"n": 0}

        def _fake_read(_params):
            calls["n"] += 1
            return 7

        monkeypatch.setattr(qc, "read_cache_epoch", _fake_read)
        reader = CacheEpochReader(conn_params={}, ttl_seconds=1000.0)
        assert reader.current() == 7
        assert reader.current() == 7
        assert calls["n"] == 1                      # second call served from TTL cache


# ---------------------------------------------------------------------------
# Pipeline cacheability glue (no heavy construction)
# ---------------------------------------------------------------------------

class TestPipelineCacheGlue:
    def _pipeline_shell(self, cache):
        from src.pipelines.xbrl.retrieval_pipeline import XbrlRetrievalPipeline
        p = XbrlRetrievalPipeline.__new__(XbrlRetrievalPipeline)
        p.query_cache = cache
        p._cache_epoch = None
        return p

    def _response(self, status="OK", halted=False):
        from src.retriever.xbrl.llm_xbrl_synthesizer import XbrlRagResponse
        return XbrlRagResponse(
            query="q", answer_summary="A", answer_details=[],
            metadata={"halted": halted} if halted else {},
            plan={}, execution_result={"status": status},
        )

    def test_cache_put_then_get_roundtrip(self):
        p = self._pipeline_shell(SemanticQueryCache(enabled=True))
        resp = self._response()
        p._cache_put("What was NVDA revenue?", resp)
        got = p._cache_get("what was nvda revenue?")
        assert got is not None and got.answer_summary == "A"

    def test_halted_and_error_responses_not_cached(self):
        p = self._pipeline_shell(SemanticQueryCache(enabled=True))
        p._cache_put("q1", self._response(halted=True))
        p._cache_put("q2", self._response(status="ERROR"))
        assert p._cache_get("q1") is None
        assert p._cache_get("q2") is None

    def test_disabled_cache_get_is_none(self):
        p = self._pipeline_shell(SemanticQueryCache(enabled=False))
        p._cache_put("q", self._response())
        assert p._cache_get("q") is None
