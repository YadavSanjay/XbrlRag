"""Unit tests for the XbrlRag-side RemoteReranker client (no network, no model).

Verifies the client posts the right payload to the XbrlRagReranker service, parses
aligned scores, and honors the fallback / error policy.
"""
from __future__ import annotations

import pytest

from src.retriever import remote_reranker as rr_mod
from src.retriever.remote_reranker import RemoteReranker


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_requires_url(monkeypatch):
    monkeypatch.delenv("RAGINFER_URL", raising=False)
    with pytest.raises(ValueError):
        RemoteReranker()


def test_resolves_endpoint_from_base(monkeypatch):
    monkeypatch.setenv("RAGINFER_URL", "http://svc:8799/")
    rr = RemoteReranker()
    assert rr.url == "http://svc:8799/rerank"


def test_posts_and_parses_scores(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({"scores": [0.9, 0.1], "model": "jinaai/jina-reranker-v3", "count": 2})

    monkeypatch.setattr(rr_mod.httpx, "post", fake_post)
    rr = RemoteReranker(url="http://svc/rerank")
    out = rr.rerank("q", ["doc a", "doc b"])
    assert out == [0.9, 0.1]
    assert captured["url"] == "http://svc/rerank"
    assert captured["json"]["query"] == "q"
    assert captured["json"]["documents"] == ["doc a", "doc b"]


def test_empty_documents_short_circuits(monkeypatch):
    def fail_post(*a, **k):  # must not be called
        raise AssertionError("should not POST for empty documents")

    monkeypatch.setattr(rr_mod.httpx, "post", fail_post)
    assert RemoteReranker(url="http://svc/rerank").rerank("q", []) == []


def test_score_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(rr_mod.httpx, "post",
                        lambda *a, **k: _FakeResp({"scores": [0.5]}))  # 1 score for 2 docs
    with pytest.raises(Exception):
        RemoteReranker(url="http://svc/rerank").rerank("q", ["a", "b"])


def test_fallback_to_local_on_failure(monkeypatch):
    monkeypatch.setenv("RAGINFER_FALLBACK_LOCAL", "1")

    def boom(*a, **k):
        raise RuntimeError("service down")

    monkeypatch.setattr(rr_mod.httpx, "post", boom)

    class _LocalStub:
        def rerank(self, q, docs):
            return [1.0 for _ in docs]

    rr = RemoteReranker(url="http://svc/rerank")
    monkeypatch.setattr(rr, "_local_reranker", lambda: _LocalStub())
    assert rr.rerank("q", ["a", "b"]) == [1.0, 1.0]
