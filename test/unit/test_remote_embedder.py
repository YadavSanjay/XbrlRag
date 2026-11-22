"""Unit tests for the XbrlRag-side RemoteEmbedder client (no network, no model).

Verifies the client posts texts to the RagInference /embed endpoint and rebuilds
the DenseEmbedding / SparseEmbedding dataclasses the retriever path expects.
"""
from __future__ import annotations

import pytest

from src.embeddings import remote_embedder as re_mod
from src.embeddings.base import DenseEmbedding, SparseEmbedding
from src.embeddings.remote_embedder import RemoteEmbedder


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
        RemoteEmbedder()


def test_endpoint_resolved_from_base(monkeypatch):
    monkeypatch.setenv("RAGINFER_URL", "http://svc:8799")
    assert RemoteEmbedder().url == "http://svc:8799/embed"


def test_embed_texts_rebuilds_dataclasses(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({
            "model": "BAAI/bge-m3",
            "count": 1,
            "embeddings": [
                {"dense": [0.1, 0.2, 0.3], "sparse": {"indices": [5, 9], "values": [0.7, 0.3]}}
            ],
        })

    monkeypatch.setattr(re_mod.httpx, "post", fake_post)
    emb = RemoteEmbedder(url="http://svc/embed")
    out = emb.embed_texts([("query", "what drove revenue?")])

    assert captured["json"]["texts"] == ["what drove revenue?"]
    dense, sparse = out["query"]
    assert isinstance(dense, DenseEmbedding) and dense.values == [0.1, 0.2, 0.3]
    assert isinstance(sparse, SparseEmbedding)
    assert sparse.indices == [5, 9] and sparse.values == [0.7, 0.3]


def test_embed_texts_empty_short_circuits(monkeypatch):
    def fail_post(*a, **k):
        raise AssertionError("should not POST for empty input")

    monkeypatch.setattr(re_mod.httpx, "post", fail_post)
    assert RemoteEmbedder(url="http://svc/embed").embed_texts([]) == {}


def test_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(re_mod.httpx, "post",
                        lambda *a, **k: _FakeResp({"embeddings": []}))  # 0 for 1 text
    with pytest.raises(Exception):
        RemoteEmbedder(url="http://svc/embed").embed_texts([("q", "text")])
