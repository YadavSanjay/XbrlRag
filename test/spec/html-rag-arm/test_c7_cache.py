"""C7: the score cache. Its file is real, in a temporary directory."""

from __future__ import annotations

import threading

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.retriever.html10k.rerank_cache import CachedReranker

MODEL = "jinaai/jina-reranker-v3"
DOCS = ["doc one", "doc two"]


def _hit_after(path, inner, query="q", docs=DOCS, model=MODEL):
    return CachedReranker(inner, model=model, cache_path=path).rerank(query, docs)


@pytest.mark.criterion("C7")
def test_a_repeat_makes_no_call(tmp_path, fake_reranker):
    # oracle: requirements 13-14 — the same request, in one object and then in a new one
    path = tmp_path / "nested" / "rerank.sqlite3"
    inner = fake_reranker({"one": 0.75, "two": 0.25})
    cached = CachedReranker(inner, model=MODEL, cache_path=path)
    first = cached.rerank("q", DOCS)
    second = cached.rerank("q", DOCS)
    third = CachedReranker(inner, model=MODEL, cache_path=path).rerank("q", DOCS)
    assert len(inner.calls) == 1
    assert first.cache_hit is False
    assert second.cache_hit is True and third.cache_hit is True
    # tolerance: exact binary fractions, stored and read back
    assert first.scores == pytest.approx([0.75, 0.25], abs=1e-12)
    assert second.scores == first.scores and third.scores == first.scores
    # The original rerank's time, not the time of reading it back.
    assert first.elapsed_ms >= 0
    assert second.elapsed_ms == first.elapsed_ms and third.elapsed_ms == first.elapsed_ms


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"query": "other"}, id="query"),
        pytest.param({"docs": ["doc one", "doc 2"]}, id="document"),
        pytest.param({"docs": ["doc two", "doc one"]}, id="order"),
        pytest.param({"model": "BAAI/bge-reranker-v2-m3"}, id="model"),
    ],
)
def test_any_change_is_a_miss(tmp_path, fake_reranker, change):
    # oracle: requirement 13 — the key is the model, the query and the ordered documents
    path = tmp_path / "rerank.sqlite3"
    inner = fake_reranker()
    _hit_after(path, inner)
    result = _hit_after(path, inner, **change)
    assert result.cache_hit is False
    assert len(inner.calls) == 2


@pytest.mark.criterion("C7")
def test_nothing_to_rank_makes_no_call(tmp_path, fake_reranker):
    # oracle: requirement 14 — the scores of nothing, from a real result object
    inner = fake_reranker()
    result = _hit_after(tmp_path / "c.sqlite3", inner, docs=[])
    assert isinstance(result.scores, list) and len(result.scores) == 0
    assert inner.calls == []


@pytest.mark.criterion("C7")
@pytest.mark.parametrize(
    "raw",
    [pytest.param([0.5], id="too-few"), pytest.param([0.5, 0.5, 0.5], id="too-many"),
     pytest.param([0.5, float("nan")], id="nan"), pytest.param([float("inf"), 0.5], id="inf")],
)
def test_bad_scores_raise_and_are_not_kept(tmp_path, fake_reranker, raw):
    # oracle: requirement 14 — a good reranker on the same file then gets called
    path = tmp_path / "rerank.sqlite3"
    with pytest.raises(ValueError):
        _hit_after(path, fake_reranker(raw=raw))
    good = fake_reranker()
    assert _hit_after(path, good).cache_hit is False
    assert len(good.calls) == 1


@pytest.mark.criterion("C7")
def test_a_failure_propagates_and_is_not_kept(tmp_path, fake_reranker):
    # oracle: requirement 14
    path = tmp_path / "rerank.sqlite3"
    with pytest.raises(RuntimeError, match="down"):
        _hit_after(path, fake_reranker(error=RuntimeError("down")))
    assert _hit_after(path, fake_reranker()).cache_hit is False


def _together(calls) -> None:
    """Runs the calls in threads from one barrier; re-raises the first failure here."""
    barrier = threading.Barrier(len(calls))
    errors: list[BaseException] = []

    def run(call) -> None:
        barrier.wait()
        try:
            call()
        except BaseException as exc:  # re-raised on the test's own thread
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if errors:
        raise errors[0]


@pytest.mark.criterion("C7")
def test_two_misses_never_overlap(tmp_path, fake_reranker):
    # oracle: requirement 15 — the fake holds each call open long enough for a second to show
    inner = fake_reranker(hold_s=0.2)
    cached = CachedReranker(inner, model=MODEL, cache_path=tmp_path / "rerank.sqlite3")
    _together([lambda: cached.rerank("q1", DOCS), lambda: cached.rerank("q2", DOCS)])
    assert len(inner.calls) == 2
    assert inner.max_in_flight == 1


@pytest.mark.criterion("C7")
def test_two_equal_calls_rerank_once(tmp_path, fake_reranker):
    # oracle: requirement 15 — the second waits, then reads what the first stored
    inner = fake_reranker(hold_s=0.2)
    cached = CachedReranker(inner, model=MODEL, cache_path=tmp_path / "rerank.sqlite3")
    results: list = []
    _together([lambda: results.append(cached.rerank("q", DOCS))] * 2)
    assert len(inner.calls) == 1
    assert sorted(r.cache_hit for r in results) == [False, True]


@pytest.mark.criterion("C7")
@given(
    scores=st.lists(
        st.floats(min_value=-10, max_value=10, allow_nan=False, allow_infinity=False),
        min_size=1, max_size=6,
    )
)
def test_a_stored_result_reads_back_exactly(tmp_path_factory, fake_reranker, scores):
    # oracle: requirement 14 — the reranker's own output; equality is exact, because a cache
    # that rounds would change a run's order
    path = tmp_path_factory.mktemp("cache") / "rerank.sqlite3"
    docs = [f"doc {i}" for i in range(len(scores))]
    first = _hit_after(path, fake_reranker(raw=scores), docs=docs)
    second = _hit_after(path, fake_reranker(raw=[0.0] * len(scores)), docs=docs)
    assert first.scores == scores
    assert second.cache_hit is True
    assert second.scores == scores
